"""Descriptor-relative file capabilities for the host CC tool server.

Only this single-threaded server writes workspace. Roots must be quiescent with
respect to other host writers; this is not protection from a hostile same-UID
process. Model programs receive a separate sandbox copy, never these roots.
"""

import contextlib
import hashlib
import os
import stat
import uuid
from pathlib import Path

from .improver_files import MAX_BYTES, MAX_FILES, read_tree

FILE_BYTES = 1024 * 1024
DEPTH = 32


def parts(path):
    if (
        not isinstance(path, str)
        or len(path.encode()) > 2048
        or any(ord(c) < 32 or c in "\\:~" for c in path)
    ):
        raise ValueError("invalid relative tool path")
    bits = path.split("/")
    if (
        bits[0] not in ("context", "workspace")
        or any(p in ("", ".", "..") or len(p.encode()) > 255 for p in bits)
        or len(bits) > DEPTH
    ):
        raise ValueError("path must be context/... or workspace/... without traversal")
    return bits


def open_dir(path):
    """Walk even the root's parents without following symlinks."""
    path = Path(path).absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular(info, limit=FILE_BYTES):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("only bounded single-link regular files are allowed")


class Files:
    def __init__(self, context, workspace):
        self.roots = {"context": Path(context), "workspace": Path(workspace)}
        self.fds = {}
        self.written = 0
        try:
            for key, root in self.roots.items():
                self.fds[key] = open_dir(root)
                tree = read_tree(root)  # Initial byte, entry, depth and file-kind budget.
                if key == "workspace":
                    self.written = sum(map(len, tree.values()))
            # Count directories as well as files; new writes never delete entries.
            self.entries = sum(1 for _ in self.roots["workspace"].rglob("*"))
        except BaseException:
            self.close()
            raise

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    @contextlib.contextmanager
    def directory(self, bits, *, create=False):
        fd = os.dup(self.fds[bits[0]])
        try:
            for name in bits[1:]:
                if create:
                    try:
                        if self.entries >= MAX_FILES:
                            raise ValueError("workspace entry budget exceeded")
                        os.mkdir(name, mode=0o700, dir_fd=fd)
                        self.entries += 1
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def list_dir(self, path):
        with self.directory(parts(path)) as fd, os.scandir(fd) as entries:
            out = []
            for entry in entries:
                if len(out) >= MAX_FILES:
                    raise ValueError("listing entry budget exceeded")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    kind = "directory"
                else:
                    regular(info, MAX_BYTES)
                    kind = "file"
                out.append({"name": entry.name, "kind": kind, "bytes": info.st_size})
            return {"path": path, "entries": sorted(out, key=lambda v: v["name"])}

    def read(self, path):
        bits = parts(path)
        if len(bits) < 2:
            raise ValueError("a file path is required")
        with self.directory(bits[:-1]) as parent:
            regular(os.stat(bits[-1], dir_fd=parent, follow_symlinks=False))
            fd = os.open(bits[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as file:
                before = os.fstat(file.fileno())
                regular(before)
                raw = file.read(FILE_BYTES + 1)
                after = os.fstat(file.fileno())
                fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if (
                    any(getattr(before, k) != getattr(after, k) for k in fields)
                    or len(raw) != before.st_size
                    or len(raw) > FILE_BYTES
                ):
                    raise ValueError("file changed or exceeded read budget")
                return raw

    def read_file(self, path):
        raw = self.read(path)
        return {
            "path": path,
            "content": raw.decode("utf-8"),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def write_file(self, path, content):
        bits = parts(path)
        if bits[0] != "workspace" or len(bits) < 2:
            raise ValueError("writes require workspace/... (context is read-only)")
        if not isinstance(content, str):
            raise ValueError("content must be UTF-8 text")
        raw = content.encode()
        if b"\0" in raw or len(raw) > FILE_BYTES or self.written + len(raw) > MAX_BYTES:
            raise ValueError("write byte budget exceeded or NUL in text")
        # Charge before side effects, including any failed writes.
        self.written += len(raw)
        with self.directory(bits[:-1], create=True) as parent:
            try:
                regular(os.stat(bits[-1], dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                if self.entries >= MAX_FILES:
                    raise ValueError("workspace entry budget exceeded")
                self.entries += 1
            tmp = ".h2-write-" + uuid.uuid4().hex
            fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp, bits[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(tmp, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
