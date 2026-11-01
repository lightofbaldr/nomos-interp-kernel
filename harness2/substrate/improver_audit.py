"""Host-owned attempt records beside (never inside) untrusted retained scratch.

No scratch traversal: a model may have left symlinks, pipes or other hostile files.
Only the checked harvest is reusable. These records bind the captured, bounded
UTF-8-decoded process streams, not the mutable raw workspace or a complete trace
after an abrupt supervisor/host failure.
"""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

from .improver_files import plain_directory, read_file

SCHEMA = "harness2.improver-attempt/1"
MAX_STREAM_BYTES = 16 * 1024**2  # Allows UTF-8 replacement of the 4 MiB process cap.


def _json(obj):
    return (json.dumps(obj, indent=2, allow_nan=False) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _put(path, raw):
    # Fresh owned directory; exclusive file creation, no linked-file overwrite.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    return _sha(raw)


class AttemptAudit:
    def __init__(self, root, *, label, cache_key, attempt, isolation, mission, system):
        self.root = plain_directory(root)
        parent = self.root / "_attempts"
        parent.mkdir(mode=0o700, exist_ok=True)
        plain_directory(parent)
        info = parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("improver attempt parent must be owner-private")
        self.path = Path(tempfile.mkdtemp(prefix="attempt-", dir=parent))
        self.work = self.path / "work"
        self.work.mkdir(mode=0o700)
        self.bindings = {
            "mission.txt": _put(self.path / "mission.txt", mission.encode()),
            "system.txt": _put(self.path / "system.txt", system.encode()),
            "started.json": _put(
                self.path / "started.json",
                _json(
                    {
                        "schema": SCHEMA,
                        "label": label,
                        "cache_key": cache_key,
                        "attempt": attempt,
                        "sandbox": isolation,
                        "started_unix": time.time(),
                    }
                ),
            ),
        }

    def finish(self, *, outcome, error, transcript):
        process = None
        if transcript is not None:
            if not isinstance(transcript, dict) or set(transcript) != {
                "stdout",
                "stderr",
                "returncode",
                "reason",
                "seconds",
                "teardown",
            }:
                raise ValueError("invalid improver transcript")
            if (
                type(transcript["returncode"]) is not int
                or type(transcript["seconds"]) not in (int, float)
                or not math.isfinite(transcript["seconds"])
                or transcript["seconds"] < 0
                or transcript["teardown"] != "confirmed"
                or (transcript["reason"] is not None and not isinstance(transcript["reason"], str))
            ):
                raise ValueError("invalid improver process record")
            for key, name in (("stdout", "stdout.jsonl"), ("stderr", "stderr.txt")):
                text = transcript[key]
                if not isinstance(text, str):
                    raise ValueError("invalid improver transcript stream")
                raw = text.encode()
                if len(raw) > MAX_STREAM_BYTES:
                    raise ValueError("improver transcript exceeds process capture bound")
                self.bindings[name] = _put(self.path / name, raw)
            process = {k: v for k, v in transcript.items() if k not in ("stdout", "stderr")}
        doc = {
            "schema": SCHEMA,
            "outcome": outcome,
            "error": error,
            "finished_unix": time.time(),
            "bindings": self.bindings,
            "process": process,
            "transcript": "captured" if transcript is not None else "unavailable",
            "transcript_scope": "bounded UTF-8-decoded stdout/stderr; not a completeness guarantee",
            "scratch": "work/ retained, mutable and untrusted; not followed or hash-bound",
        }
        controls = self.path / "login-tools"
        if controls.exists():
            from .login_tools import verify_session

            doc["tool_audit_sha256"] = verify_session(controls, accepted=outcome == "accepted")
        digest = _put(self.path / "finished.json", _json(doc))
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"path": self.path.relative_to(self.root).as_posix(), "sha256": digest}


def load_attempt(root, ref):
    """Verify host records only. Never inspect or follow anything under work/."""
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ValueError("invalid improver audit reference")
    rel = Path(ref["path"])
    if (
        len(rel.parts) != 2
        or rel.parts[0] != "_attempts"
        or not rel.parts[1].startswith("attempt-")
        or rel.as_posix() != ref["path"]
    ):
        raise ValueError("invalid improver audit path")
    path = plain_directory(plain_directory(root) / rel)
    raw = read_file(path / "finished.json")
    if _sha(raw) != ref["sha256"]:
        raise ValueError("improver audit manifest changed")
    doc = json.loads(raw)
    expected = {"mission.txt", "system.txt", "started.json"}
    if doc.get("transcript") == "captured":
        expected.update(("stdout.jsonl", "stderr.txt"))
    elif doc.get("transcript") != "unavailable":
        raise ValueError("invalid improver audit transcript status")
    if doc.get("schema") != SCHEMA or set(doc.get("bindings", {})) != expected:
        raise ValueError("invalid improver audit bindings")
    for name, digest in doc["bindings"].items():
        if _sha(read_file(path / name, max_bytes=MAX_STREAM_BYTES)) != digest:
            raise ValueError("improver audit input or transcript changed")
    started = json.loads(read_file(path / "started.json"))
    login_tools = started.get("sandbox", {}).get("channel", {}).get("sandbox") == "login-tools"
    if login_tools and doc.get("outcome") == "accepted" and not doc.get("tool_audit_sha256"):
        raise ValueError("accepted login-tools attempt has no tool audit")
    if "tool_audit_sha256" in doc:
        from .login_tools import verify_session

        actual = verify_session(path / "login-tools", accepted=doc.get("outcome") == "accepted")
        if actual != doc["tool_audit_sha256"]:
            raise ValueError("login-tools session changed")
    return doc
