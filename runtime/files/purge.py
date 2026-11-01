"""Host-only conversation cleanup; never exposed as a model tool.

The app has stopped its harness and broker first. Lock all selected workspaces,
refuse an active orphaned unit, and unlink using directory descriptors only.
Empty lock files remain so their inode continues to serialize future accesses.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def directory(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    s = os.fstat(fd)
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        os.close(fd)
        raise ValueError("non-private directory")
    return fd


def remove(parent, name):
    s = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(s.st_mode):
        if s.st_dev != os.fstat(parent).st_dev:
            raise ValueError("refusing mounted child")
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for child in os.listdir(fd):
                remove(fd, child)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def purge(value, request):
    root = Path(value)
    if not root.is_absolute() or str(root) != value or root.resolve() != root or len(root.parts) < 4:
        raise ValueError("unsafe root")
    if set(request) != {"all", "ids"} or type(request["all"]) is not bool or not isinstance(request["ids"], list):
        raise ValueError("invalid request")
    ids = request["ids"]
    if len(ids) > 1000 or len(ids) != len(set(ids)) or not all(isinstance(i, str) and re.fullmatch(r"[1-9]\d{0,14}", i) for i in ids):
        raise ValueError("invalid ids")
    if not root.exists():
        return
    fds = []
    try:
        parent = directory(None, str(root)); fds.append(parent)
        if "workspaces" not in os.listdir(parent):
            return
        work = directory(parent, "workspaces"); fds.append(work)
        names = os.listdir(work)
        if request["all"]:
            # 'probe' is installation diagnostics, not a conversation.
            if any(n != "probe" and not re.fullmatch(r"[1-9]\d{0,14}", n) for n in names):
                raise ValueError("unknown workspace identity")
            ids = sorted(n for n in names if n != "probe")
        selected = [i for i in ids if i in names]
        if not selected:
            return
        locks = directory(parent, "locks"); fds.append(locks)
        for ident in selected:
            fd = os.open(ident, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=locks); fds.append(fd)
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid() or s.st_mode & 0o077:
                raise ValueError("unsafe lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            workspace = directory(work, ident); fds.append(workspace)
            unit = "glimmer-files-" + hashlib.sha256(value.encode()).hexdigest()[:16] + "-" + ident + ".service"
            result = subprocess.run(["/usr/bin/systemctl", "--user", "show", unit, "--property=LoadState,ActiveState"],
                                    capture_output=True, timeout=6, check=False)
            props = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
            if props.get("LoadState") != "not-found" and props.get("ActiveState") not in ("inactive", "failed"):
                raise ValueError("workspace unit is active or unknown")
        for ident in selected:
            remove(work, ident)
    finally:
        for fd in reversed(fds):
            os.close(fd)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        purge(sys.argv[1], json.loads(sys.stdin.buffer.read(32769)))
        print("WORKSPACES PURGED")
    except Exception:
        print("Workspace cleanup refused or incomplete.", file=sys.stderr)
        sys.exit(1)
