"""Deterministic host-owned compile/run verdict, separate from the Muse opinion."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from ..substrate.sandbox import Sandbox
from .mojo_tasks import Task

_HEALTH_LOCK = threading.Lock()
_PROBE = 'def main():\n    print("harness2-toolchain-ok")\n'


def compiler_health(sandbox: Sandbox):
    """A known-good compile and execution, cached only for the same runtime identity.

    A broken compiler/linker must never turn every candidate into a scored zero.
    This control contains no task, expectation or model-provided source.
    """
    identity = sandbox.identity()
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with _HEALTH_LOCK:
        cached = getattr(sandbox, "_mojo_health", None)
        if cached and cached["identity_sha256"] == digest:
            return cached
        with tempfile.TemporaryDirectory(prefix="h2-mojo-health-") as tmp:
            work = Path(tmp)
            (work / "solution.mojo").write_text(_PROBE)
            compiled = sandbox.run(
                work,
                ["/opt/mojo/bin/mojo", "build", "/work/solution.mojo", "-o", "/work/program"],
                timeout=90,
            )
            ran = None
            exe = work / "program"
            if (
                compiled.returncode == 0
                and not compiled.reason
                and exe.is_file()
                and not exe.is_symlink()
            ):
                ran = sandbox.run(work, ["/work/program"], timeout=10)
            healthy = bool(
                ran
                and ran.returncode == 0
                and not ran.reason
                and ran.stdout.strip() == "harness2-toolchain-ok"
            )
            record = {
                "pass": healthy,
                "identity": identity,
                "identity_sha256": digest,
                "source_sha256": hashlib.sha256(_PROBE.encode()).hexdigest(),
                "compile": asdict(compiled),
                "run": asdict(ran) if ran else None,
            }
            if healthy:
                sandbox._mojo_health = record
            return record


def grade(task: Task, code: str | None, sandbox: Sandbox, *, compile_timeout=90, run_timeout=10):
    info = {
        "task_sha256": task.sha256,
        "code_sha256": hashlib.sha256((code or "").encode()).hexdigest(),
        "kind": task.kind,
        "oracle": "compile" if task.kind == "compile" else "compile+stdout",
        "stdout_normalization": "strip leading/trailing whitespace; internal bytes preserved",
        "compile": None,
        "run": None,
        "status": "scored",
        "passed": False,
    }
    if not code or not code.strip():
        return info | {"reason": "no_program"}
    if len(code.encode()) > 1_048_576 or "\x00" in code:
        return info | {"reason": "invalid_program"}
    info["toolchain_health"] = compiler_health(sandbox)
    if not info["toolchain_health"]["pass"]:
        return info | {"status": "unscored", "passed": None, "reason": "toolchain_health"}
    with tempfile.TemporaryDirectory(prefix="h2-mojo-grade-") as tmp:
        work = Path(tmp)
        source = code + ("\n\n" + task.test if task.test else "")
        (work / "solution.mojo").write_text(source)
        compilation = sandbox.run(
            work,
            ["/opt/mojo/bin/mojo", "build", "/work/solution.mojo", "-o", "/work/program"],
            timeout=compile_timeout,
        )
        info["compile"] = asdict(compilation)
        if compilation.reason:
            return info | {"status": "unscored", "passed": None, "reason": compilation.reason}
        if compilation.returncode != 0:
            # A nonzero sandbox launch is not a failed compilation.
            if any(s in compilation.stderr for s in ("bwrap:", "Failed to", "Failed to connect")):
                return info | {"status": "unscored", "passed": None, "reason": "sandbox"}
            return info | {"reason": "compile_failed"}
        exe = work / "program"
        if not exe.is_file() or exe.is_symlink():
            return info | {"status": "unscored", "passed": None, "reason": "compiler_no_artifact"}
        if task.kind == "compile":
            return info | {"passed": True, "reason": None}
        execution = sandbox.run(work, ["/work/program"], timeout=run_timeout)
        info["run"] = asdict(execution)
        if execution.reason:
            return info | {"reason": execution.reason}
        ok = execution.returncode == 0 and execution.stdout.strip() == task.expect_stdout.strip()
        return info | {"passed": ok, "reason": None if ok else "runtime_or_stdout"}
