"""Versioned, fixed-command Linux file-tool broker (no model code on the host).

Policy adapted from Harness2 sandbox.py at 1bc5c61. A transient *service*, not
a terminal scope, owns the worker. Only the selected workspace is persistent RW.
Resource properties are read back before the request is released to the worker.
"""
import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

WORKER = Path(__file__).with_name("worker.py").resolve(strict=True)
POLICY = "glimmer.files/bwrap-1"
ENV = {k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ}
ENV.update(PATH="/usr/bin:/bin", LANG="C.UTF-8")
PROPERTIES = {
    "MemoryMax": "268435456", "MemorySwapMax": "0", "TasksMax": "16", "CPUQuota": "100%",
    "LimitFSIZE": "1048576", "LimitNOFILE": "128", "RuntimeMaxSec": "30",
    "TimeoutStopSec": "2", "KillMode": "control-group", "OOMPolicy": "stop", "NoNewPrivileges": "yes",
}


class Refusal(Exception):
    pass


def control(*args):
    return subprocess.run(["/usr/bin/systemctl", "--user", *args], env=ENV, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=6, check=False)


def stop(unit):
    # stop waits for the entire cgroup. A collected/never-started unit is also settled.
    control("stop", unit)
    info = control("show", unit, "--property=LoadState,ActiveState").stdout.decode()
    props = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
    if props.get("LoadState") != "not-found" and props.get("ActiveState") not in ("inactive", "failed"):
        raise Refusal("sandbox cleanup could not be confirmed; tools unavailable")


def private_root(value):
    p = Path(value)
    if not p.is_absolute() or str(p) != value or len(p.parts) < 4 or p.resolve() != p:
        raise Refusal("workspace root must be an absolute, non-symlink private directory")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.resolve(strict=True) != p:
        raise Refusal("workspace root changed")
    s = p.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise Refusal("workspace root must be owner-private (0700)")
    return p


def childdir(parent, name):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    s = os.fstat(fd)
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        os.close(fd)
        raise Refusal("workspace directory must be owner-private")
    return fd


def argv(source, unit):
    command = ["/usr/bin/systemd-run", "--user", "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec", "--unit=" + unit]
    command += ["--property=" + k + "=" + v for k, v in PROPERTIES.items()]
    command += ["--", "/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--assert-userns-disabled",
                "--new-session", "--die-with-parent", "--cap-drop", "ALL", "--clearenv", "--ro-bind", "/usr", "/usr",
                "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                "--proc", "/proc", "--dev", "/dev", "--dir", "/home", "--dir", "/etc", "--dir", "/run",
                "--bind", source, "/work", "--ro-bind", str(WORKER), "/runtime/worker.py",
                "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8", "--setenv", "HOME", "/home",
                "--chdir", "/work", "--remount-ro", "/", "--", "/usr/bin/python3", "-I", "-B", "/runtime/worker.py"]
    return command


def verify_ready(line, unit):
    ready = json.loads(line).get("ready")
    keys = {"root_ro", "runtime_ro", "work_rw", "caps_zero", "no_new_privs", "home_empty", "run_empty", "cwd", "env", "fsize"}
    if not isinstance(ready, dict) or set(ready.get("checks", {})) != keys or any(v is not True for v in ready["checks"].values()):
        raise Refusal("sandbox isolation probe failed")
    for ns in ("net", "pid"):
        value = ready.get(ns + "ns")
        if not isinstance(value, str) or not re.fullmatch(ns + r":\[\d+\]", value) or value == os.readlink("/proc/self/ns/" + ns):
            raise Refusal("sandbox namespace not isolated")
    expected = {"MemoryMax": "268435456", "MemorySwapMax": "0", "TasksMax": "16", "LimitFSIZE": "1048576",
                "LimitNOFILE": "128", "KillMode": "control-group", "OOMPolicy": "stop", "NoNewPrivileges": "yes",
                "RuntimeMaxUSec": "30s", "TimeoutStopUSec": "2s", "CPUQuotaPerSecUSec": "1s", "ActiveState": "active"}
    response = control("show", unit, "--property=" + ",".join(expected))
    got = dict(line.split("=", 1) for line in response.stdout.decode().splitlines() if "=" in line)
    if response.returncode != 0 or any(got.get(k) != v for k, v in expected.items()):
        raise Refusal("sandbox cgroup/resource properties not confirmed")
    return {"isolation": ready, "resources": got}


def run(work_fd, source, unit, request):
    stop(unit)  # orphan from a killed broker must settle before a new operation
    if Path(source).resolve(strict=True) != Path(source) or (os.stat(source).st_dev, os.stat(source).st_ino) != (os.fstat(work_fd).st_dev, os.fstat(work_fd).st_ino):
        raise Refusal("workspace path changed")
    # The parent/root are operator-private and not mounted in the worker. Workers cannot
    # rename or replace this source. Same-UID host writers must remain quiescent.
    proc = subprocess.Popen(argv(source, unit), env=ENV, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    evidence = None
    output = {"out": bytearray(), "err": bytearray()}
    ready = False
    sending = memoryview(json.dumps(request, ensure_ascii=False, allow_nan=False).encode() + b"\n")
    deadline = time.monotonic() + 35
    try:
        with selectors.DefaultSelector() as sel:
            for name, stream in (("out", proc.stdout), ("err", proc.stderr)):
                os.set_blocking(stream.fileno(), False)
                sel.register(stream, selectors.EVENT_READ, name)
            while sel.get_map():
                if time.monotonic() > deadline:
                    raise Refusal("sandbox deadline exceeded")
                for key, _ in sel.select(0.1):
                    if key.data == "in":
                        try:
                            count = os.write(key.fd, sending[:16384])
                            sending = sending[count:]
                        except BlockingIOError:
                            continue
                        if not sending:
                            sel.unregister(key.fileobj)
                            proc.stdin.close()
                        continue
                    data = os.read(key.fd, 8192)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    output[key.data].extend(data)
                    if sum(map(len, output.values())) > 73728:
                        raise Refusal("sandbox output limit exceeded")
                    if not ready and b"\n" in output["out"]:
                        line, tail = output["out"].split(b"\n", 1)
                        evidence = verify_ready(line, unit)
                        output["out"] = bytearray(tail)
                        ready = True
                        os.set_blocking(proc.stdin.fileno(), False)
                        sel.register(proc.stdin, selectors.EVENT_WRITE, "in")
            proc.wait(timeout=3)
        if proc.returncode != 0 or not ready:
            raise Refusal("sandbox unavailable or stopped by a resource limit")
        result = json.loads(output["out"])
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise Refusal("invalid sandbox result")
        return {**result, "policy": POLICY, "unit": unit, "evidence": evidence}
    finally:
        # Mandatory cleanup is not cancelled by a second TERM.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            stop(unit)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


@contextmanager
def locked_workspace(value, ident):
    if not (ident == "probe" or ident.isdigit() and 0 < int(ident) < 10**15):
        raise Refusal("invalid workspace identity")
    root = private_root(value)
    unit = "glimmer-files-" + hashlib.sha256(str(root).encode()).hexdigest()[:16] + "-" + ident + ".service"
    fds = []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(parent)
        locks = childdir(parent, "locks")
        fds.append(locks)
        lock = os.open(ident, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=locks)
        fds.append(lock)
        s = os.fstat(lock)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid():
            raise Refusal("invalid workspace lock")
        end = time.monotonic() + 35
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > end:
                    raise Refusal("workspace busy")
                time.sleep(0.05)
        workspaces = childdir(parent, "workspaces")
        fds.append(workspaces)
        workspace = childdir(workspaces, ident)
        fds.append(workspace)
        yield workspace, str(root / "workspaces" / ident), unit
    finally:
        for fd in reversed(fds):
            os.close(fd)


def main():
    if len(sys.argv) != 3:
        raise Refusal("invalid broker arguments")
    request = json.loads(sys.stdin.buffer.read(2097153))
    if len(json.dumps(request, ensure_ascii=False).encode()) > 2097152:
        raise Refusal("request limit")
    if sys.argv[2] == "probe":
        request = {"op": "probe", "args": {}}
    elif not isinstance(request, dict) or request.get("op") not in ("read", "write", "edit", "ls", "find", "grep"):
        raise Refusal("unknown operation")
    with locked_workspace(sys.argv[1], sys.argv[2]) as (workspace, source, unit):
        result = run(workspace, source, unit, request)
    print(json.dumps(result, allow_nan=False), flush=True)


def cancelled(_signum, _frame):
    raise Refusal("operation cancelled; a mutation may have completed; inspect before retrying")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    try:
        main()
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as e:
        print(json.dumps({"ok": False, "error": str(e)[:500], "unavailable": True, "policy": POLICY}), flush=True)
        sys.exit(3)
