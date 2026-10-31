"""Bounded, non-following snapshots at the untrusted improver/host boundary."""

import os
import shutil
import stat
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 4096


def read_file(path, *, max_bytes=MAX_BYTES):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("linked artifact")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
            raise ValueError("artifact must be a bounded single-link regular file")
        with os.fdopen(fd, "rb", closefd=False) as file:
            data = file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("oversized artifact")
        return data
    finally:
        os.close(fd)


def plain_directory(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_dir():
        raise ValueError("linked or missing improver directory")
    return path


def read_tree(root, *, max_bytes=MAX_BYTES, max_files=MAX_FILES):
    """Read regular, single-link files only; bound traversal, memory and depth.

    Writers must be stopped. Descriptor-relative opens additionally fail closed
    on replaced entries; this is not a snapshot against a hostile host writer.
    """
    root = plain_directory(root)
    result, total, entries = {}, 0, 0

    def walk(fd, prefix, depth):
        nonlocal total, entries
        if depth > 32:
            raise ValueError("improver tree too deep")
        with os.scandir(fd) as scan:
            for entry in scan:
                entries += 1
                if entries > max_files:
                    raise ValueError("improver tree entry budget exceeded")
                info = entry.stat(follow_symlinks=False)
                name = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                    )
                    try:
                        walk(child, name + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                    )
                    try:
                        current = os.fstat(child)
                        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                            raise ValueError("unsafe improver artifact")
                        if current.st_size > max_bytes - total:
                            raise ValueError("improver tree byte budget exceeded")
                        with os.fdopen(child, "rb", closefd=False) as file:
                            data = file.read(max_bytes - total + 1)
                        total += len(data)
                        if total > max_bytes or len(data) != current.st_size:
                            raise ValueError("oversized or changing improver artifact")
                        result[name] = data
                    finally:
                        os.close(child)
                else:
                    raise ValueError("links and special files are forbidden in improver artifacts")

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(fd, "", 0)
    finally:
        os.close(fd)
    return result


def write_tree(data, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    for name, content in data.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(content)
        path.chmod(0o600)


def copy_tree(source, dest):
    write_tree(read_tree(source), dest)


def collect(source_root, subpath, dest):
    rel = Path(subpath)
    if rel.is_absolute() or not rel.parts or any(part in ("..", ".") for part in rel.parts):
        raise ValueError("unsafe improver collection path")
    root = plain_directory(source_root)
    source = root / rel
    # Check containment before is_dir: a dangling or outside link is not 'no output'.
    if source.resolve() != source or not source.resolve().is_relative_to(root):
        raise ValueError("linked improver collection path")
    if not source.exists():
        return None
    data = read_tree(source)
    dest = Path(dest)
    if dest.is_symlink():
        raise ValueError("linked collection destination")
    dest.parent.mkdir(parents=True, exist_ok=True)
    plain_directory(dest.parent)
    if dest.exists():
        plain_directory(dest)
        shutil.rmtree(dest)  # only this exact previous collection, after validating the new one
    write_tree(data, dest)
    return dest
