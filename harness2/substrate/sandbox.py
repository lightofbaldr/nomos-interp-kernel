"""Fail-closed Linux bubblewrap runner. No host-home mount or inherited environment.

All untrusted children (Pi, extensions, compiler and program) run inside a transient
user service with cgroup memory/process/time limits. Only /work is persistent RW.
Compile/run invocations have no broker and no network at all.
"""

from __future__ import annotations

import hashlib
import os
import selectors
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ENTRY = Path(__file__).with_name("sandbox_entry.py")
WORKSPACE_BYTES = 256 * 1024**2
WORKSPACE_ENTRIES = 8192
HOST_ENV = {
    k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ
}
HOST_ENV.update({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    reason: str | None = None


def real_dir(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path or not path.is_dir():
        raise ValueError("sandbox directory must exist and have no symlink components")
    return path


def workspace_over_budget(work: Path) -> bool:
    """Bound the scan and never follow links. This is a watchdog, not a disk quota."""
    total = count = 0

    def walk(fd, depth):
        nonlocal total, count
        if depth > 64:
            return True
        with os.scandir(fd) as entries:
            for entry in entries:
                count += 1
                if count > WORKSPACE_ENTRIES:
                    return True
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child = os.open(
                            entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            if walk(child, depth + 1):
                                return True
                        finally:
                            os.close(child)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except FileNotFoundError:
                    continue
                if total > WORKSPACE_BYTES:
                    return True
        return False

    try:
        root = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return walk(root, 0)
        finally:
            os.close(root)
    except OSError:
        return True


class Sandbox:
    def __init__(
        self, *, mojo: Path | None = None, node: Path | None = None, pi: Path | None = None
    ):
        self.mojo = real_dir(mojo) if mojo is not None else None
        self.node = Path(node).resolve(strict=True) if node is not None else None
        self.pi = real_dir(pi) if pi is not None else None

    def identity(self):
        files = {"bwrap": Path("/usr/bin/bwrap"), "entry": ENTRY, "sandbox": Path(__file__)}
        if self.node:
            files["node"] = self.node
        if self.pi:
            for rel in ("package.json", "dist/bundle/cli.js", "dist/bundle/cli-runtime.js"):
                files["pi/" + rel] = self.pi / rel
        if self.mojo:
            for rel in ("bin/mojo", "lib/mojo/std.mojoc", "share/max/modular.cfg"):
                files["mojo/" + rel] = self.mojo / rel
        hashes = {}
        for name, path in files.items():
            with path.open("rb") as file:
                hashes[name] = hashlib.file_digest(file, "sha256").hexdigest()
        return {
            "files": hashes,
            "memory_bytes": 4 * 1024**3,
            "tasks_max": 128,
            "workspace_watchdog_bytes": WORKSPACE_BYTES,
            "workspace_watchdog_entries": WORKSPACE_ENTRIES,
            "network": "none except optional fixed-route model broker",
            "filesystem": "system RO; private workspace RW; no host home",
        }

    def argv(self, workspace: Path, command: list[str], *, broker: Path | None = None) -> list[str]:
        work = real_dir(workspace)
        if work == Path("/") or work == Path.home() or len(work.parts) < 3:
            raise ValueError("unsafe rollout workspace")
        for rel in (".tmp", ".home", ".cache"):
            p = work / rel
            p.mkdir(exist_ok=True)
            real_dir(p)
        args = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--unshare-user",
            "--disable-userns",
            "--assert-userns-disabled",
            "--new-session",
            "--die-with-parent",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--dir",
            "/home",
            "--dir",
            "/etc",
            "--dir",
            "/run",
            "--bind",
            str(work),
            "/work",
            "--bind",
            str(work / ".tmp"),
            "/tmp",
            "--setenv",
            "HOME",
            "/work/.home",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "XDG_CACHE_HOME",
            "/work/.cache",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "PATH",
            "/opt/mojo/bin:/opt/node:/usr/bin:/bin",
            "--setenv",
            "PI_CODING_AGENT_DIR",
            "/work/.agent",
            "--setenv",
            "PI_SKIP_VERSION_CHECK",
            "1",
            "--setenv",
            "PI_TELEMETRY",
            "0",
            "--setenv",
            "NODE_COMPILE_CACHE",
            "/work/.cache/node",
            "--chdir",
            "/work",
        ]
        for name in ("cc", "c++", "cpp"):
            target = Path("/etc/alternatives") / name
            if target.exists():
                resolved = target.resolve(strict=True)
                if not resolved.is_relative_to("/usr"):
                    raise ValueError("system C toolchain outside /usr")
                args += ["--symlink", str(resolved), str(target)]
        if self.mojo:
            # Do not mount the prefix's crash database/cache. Relocate the installed
            # compiler configuration; never source its activation/telemetry script.
            modular = work / ".modular"
            modular.mkdir(exist_ok=True)
            real_dir(modular)
            body = (
                (self.mojo / "share/max/modular.cfg")
                .read_text()
                .replace(str(self.mojo), "/opt/mojo")
            )
            body = body.replace("/opt/mojo/share/max/.max_cache", "/work/.cache/mojo")
            cfg = modular / "modular.cfg"
            if cfg.is_symlink():
                raise ValueError("symlinked compiler config")
            if cfg.exists():
                if not cfg.is_file() or cfg.read_text() != body:
                    raise ValueError("compiler config changed")
            else:
                cfg.write_text(body)
            for directory in ("bin", "lib", "include"):
                if (self.mojo / directory).is_dir():
                    args += ["--ro-bind", str(self.mojo / directory), "/opt/mojo/" + directory]
            args += ["--setenv", "MODULAR_HOME", "/work/.modular"]
        if self.node:
            args += ["--ro-bind", str(self.node), "/opt/node/node"]
        if self.pi:
            args += ["--ro-bind", str(self.pi), "/opt/pi"]
        if broker:
            args += [
                "--ro-bind",
                str(broker),
                "/model.sock",
                "--ro-bind",
                str(ENTRY),
                "/runtime/entry.py",
            ]
            command = ["/usr/bin/python3", "-I", "/runtime/entry.py", "--", *command]
        # Root and mountpoint parents themselves must not become another writable surface.
        args += ["--remount-ro", "/", "--", *command]
        return args

    def run(
        self,
        workspace: Path,
        command: list[str],
        *,
        timeout=240,
        broker=None,
        output_limit=4 * 1024 * 1024,
    ) -> ProcessResult:
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("timeout must be 1..3600 seconds")
        unit = "harness2-" + uuid.uuid4().hex
        cmd = [
            "/usr/bin/systemd-run",
            "--user",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            "--service-type=exec",
            f"--unit={unit}",
            "--property=MemoryMax=4G",
            "--property=MemorySwapMax=0",
            "--property=TasksMax=128",
            "--property=CPUQuota=200%",
            "--property=LimitFSIZE=67108864",
            "--property=LimitNOFILE=256",
            f"--property=RuntimeMaxSec={timeout}",
            "--property=KillMode=control-group",
            "--property=OOMPolicy=stop",
            "--",
            *self.argv(workspace, command, broker=broker),
        ]
        start = time.monotonic()
        if workspace_over_budget(workspace):
            return ProcessResult(125, "", "workspace budget exceeded", 0, "workspace_limit")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=HOST_ENV,
                start_new_session=True,
            )
        except OSError:
            return ProcessResult(127, "", "sandbox launcher unavailable", 0, "sandbox")
        chunks = {"stdout": bytearray(), "stderr": bytearray()}
        reason = None
        next_scan = start
        try:
            with selectors.DefaultSelector() as sel:
                for name in chunks:
                    sel.register(getattr(proc, name), selectors.EVENT_READ, name)
                while sel.get_map():
                    if time.monotonic() >= next_scan:
                        next_scan = time.monotonic() + 0.5
                        if workspace_over_budget(workspace):
                            reason = "workspace_limit"
                            break
                    if time.monotonic() - start > timeout + 5:
                        reason = "timeout"
                        break
                    for key, _ in sel.select(timeout=0.1):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            sel.unregister(key.fileobj)
                            continue
                        chunks[key.data].extend(data)
                        if sum(map(len, chunks.values())) > output_limit:
                            reason = "output_limit"
                            break
                    if reason:
                        break
            if reason is None:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    reason = "timeout"
        finally:
            # Stop the complete service cgroup, not merely the foreground shell/process.
            subprocess.run(
                ["/usr/bin/systemctl", "--user", "stop", unit + ".service"],
                env=HOST_ENV,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()
        if reason is None and (proc.returncode < 0 or proc.returncode in (124, 137, 143)):
            reason = "resource_or_timeout"
        if reason is None and workspace_over_budget(workspace):
            reason = "workspace_limit"
        return ProcessResult(
            proc.returncode,
            chunks["stdout"].decode(errors="replace"),
            chunks["stderr"].decode(errors="replace"),
            time.monotonic() - start,
            reason,
        )
