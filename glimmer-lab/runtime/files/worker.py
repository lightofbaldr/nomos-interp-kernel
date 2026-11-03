"""Trusted file worker. Runs ONLY inside the broker's bwrap service; never evals input.

All path operations are descriptor-relative/no-follow. No shell, compiler, imports
from the workspace, symlink following, regex evaluation or arbitrary commands.
"""
import json
import os
import resource
import re
import stat
import sys
import uuid

FILE_MAX = 1024 * 1024
WORK_MAX = 256 * 1024 * 1024
ENTRIES_MAX = 8192
OUTPUT_MAX = 65536
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def parts(path, directory=False):
    if not isinstance(path, str) or len(path) > 1024 or any(ord(c) < 32 for c in path) or "\\" in path:
        raise ValueError("invalid workspace path")
    if path == "/work" or path == ".":
        if directory:
            return []
    if path.startswith("/work/"):
        path = path[6:]
    out = path.split("/")
    if len(out) > 32 or any(not p or p in (".", "..") or len(p.encode()) > 255 or p.startswith(".glimmer-") for p in out):
        raise ValueError("use a plain path inside /work; no traversal or reserved names")
    return out


def directory(root, names, create=False):
    fd = os.dup(root)
    try:
        for name in names:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular(fd):
    s = os.fstat(fd)
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > FILE_MAX:
        raise ValueError("requires a regular, unlinked text file up to 1 MiB")
    return s


def read_at(parent, name):
    fd = os.open(name, FLAGS, dir_fd=parent)
    try:
        before = regular(fd)
        data = bytearray()
        while len(data) <= FILE_MAX:
            chunk = os.read(fd, min(65536, FILE_MAX + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = regular(fd)
        if len(data) > FILE_MAX or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("file changed or exceeds 1 MiB")
        text = data.decode("utf-8", errors="strict")
        if "\0" in text:
            raise ValueError("binary/NUL text is unsupported")
        return text
    finally:
        os.close(fd)


def inventory(root):
    rows, total = [], 0

    def walk(fd, prefix, depth):
        nonlocal total
        if depth > 32:
            raise ValueError("workspace depth limit")
        # scandir is bounded while iterating, before sorting (not os.listdir()).
        names = []
        with os.scandir(fd) as scan:
            for entry in scan:
                names.append(entry.name)
                if len(names) + len(rows) > ENTRIES_MAX:
                    raise ValueError("workspace entry limit")
        for name in sorted(names):
            rel = prefix + name
            if name.startswith(".glimmer-"):
                # An uncommitted atomic-write temporary can survive SIGKILL. It was
                # never the destination file. Model paths cannot use this namespace.
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not re.fullmatch(r"\.glimmer-[0-9a-f]{32}", name) or not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid() or s.st_mode & 0o077:
                    raise ValueError("invalid internal staging file; operator repair required")
                os.unlink(name, dir_fd=fd)
                continue
            parts(rel)
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(s.st_mode) and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1):
                raise ValueError("workspace contains a symlink, linked file or special file")
            rows.append({"path": rel, "kind": "directory" if stat.S_ISDIR(s.st_mode) else "file", "bytes": s.st_size if stat.S_ISREG(s.st_mode) else 0})
            total += rows[-1]["bytes"]
            if len(rows) > ENTRIES_MAX or total > WORK_MAX:
                raise ValueError("workspace budget exceeded")
            if stat.S_ISDIR(s.st_mode):
                child = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=fd)
                try:
                    walk(child, rel + "/", depth + 1)
                finally:
                    os.close(child)
    walk(root, "", 0)
    return rows, total


def text_arg(value, maximum=FILE_MAX, empty=True):
    if not isinstance(value, str) or (not empty and not value) or "\0" in value or len(value.encode("utf-8", errors="strict")) > maximum:
        raise ValueError("invalid or oversized text")
    return value


def integer(value, maximum, default):
    value = default if value is None else value
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid numeric limit")
    return value


def execute(root, request):
    if not isinstance(request, dict) or set(request) != {"op", "args"}:
        raise ValueError("invalid worker request")
    op, args = request["op"], request["args"]
    allowed = {"read": {"path", "offset", "limit"}, "write": {"path", "content"}, "edit": {"path", "oldText", "newText"}, "ls": {"path"}, "find": {"path", "name"}, "grep": {"path", "text"}}
    if op not in allowed or not isinstance(args, dict) or set(args) - allowed[op]:
        raise ValueError("unknown tool/argument")
    names = parts(args.get("path", "."), directory=op in ("ls", "find", "grep"))
    rows, total = inventory(root)
    if op in ("ls", "find", "grep"):
        # Resolve requested directory, even when no entries would match.
        fd = directory(root, names)
        os.close(fd)
        prefix = "/".join(names) + ("/" if names else "")
        selected = [r for r in rows if r["path"].startswith(prefix)]
        result = []
        term = text_arg(args.get("name") if op == "find" else args.get("text"), 160, False) if op != "ls" else ""
        for row in selected:
            rel = row["path"]
            if op == "ls" and "/" not in rel[len(prefix):] or op == "find" and term in rel.split("/")[-1]:
                result.append(row)
            elif op == "grep" and row["kind"] == "file":
                parent = directory(root, parts(rel)[:-1])
                try:
                    text = read_at(parent, parts(rel)[-1])
                finally:
                    os.close(parent)
                for line_no, line in enumerate(text.splitlines(), 1):
                    if term in line:
                        result.append({"path": rel, "line": line_no, "text": line[:240], "lineTruncated": len(line) > 240})
                        if len(result) >= 200:
                            break
            if len(result) >= 200:
                break
        return {"entries": result, "limitReached": len(result) >= 200}
    if op == "write":
        present = {r["path"] for r in rows}
        missing = sum("/".join(names[:n]) not in present for n in range(1, len(names)))
        if len(rows) + missing + 1 > ENTRIES_MAX:
            raise ValueError("workspace entry budget exceeded")
    parent = directory(root, names[:-1], create=op == "write")
    try:
        if op == "read":
            text = read_at(parent, names[-1])
            offset = integer(args.get("offset"), FILE_MAX, 0)
            limit = integer(args.get("limit"), 12000, 12000)
            out = text[offset:offset + limit]
            return {"text": out, "nextOffset": offset + len(out), "truncated": offset + len(out) < len(text), "offsetUnits": "Unicode characters"}
        if op == "edit":
            old = text_arg(args.get("oldText"), empty=False)
            new = text_arg(args.get("newText"))
            original = read_at(parent, names[-1])
            if original.count(old) != 1:
                raise ValueError("edit needs exactly one matching oldText")
            content = original.replace(old, new, 1)
        else:
            content = text_arg(args.get("content"))
        encoded = text_arg(content).encode()
        previous = 0
        try:
            existing = os.open(names[-1], FLAGS, dir_fd=parent)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            try:
                previous = regular(existing).st_size
            finally:
                os.close(existing)
        rows, total = inventory(root)  # includes newly created parent directories
        if total - previous + len(encoded) > WORK_MAX or len(rows) + 1 > ENTRIES_MAX:
            raise ValueError("workspace budget exceeded")
        temp = ".glimmer-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, names[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass
        return {"path": "/work/" + "/".join(names), "bytes": len(encoded), "written": True}
    finally:
        os.close(parent)


def isolation():
    status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
    mounts = {line.split()[4]: line.split()[5].split(",") for line in open("/proc/self/mountinfo")}
    checks = {
        "root_ro": "ro" in mounts.get("/", []), "runtime_ro": "ro" in mounts.get("/usr", []),
        "work_rw": "rw" in mounts.get("/work", []), "caps_zero": int(status["CapEff"].strip(), 16) == 0,
        "no_new_privs": status["NoNewPrivs"].strip() == "1", "home_empty": os.listdir("/home") == [],
        "run_empty": os.listdir("/run") == [], "cwd": os.getcwd() == "/work",
        "env": set(os.environ) <= {"PATH", "LANG", "HOME", "LC_CTYPE", "PWD"},
        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE) == (FILE_MAX, FILE_MAX),
    }
    return {"checks": checks, "netns": os.readlink("/proc/self/ns/net"), "pidns": os.readlink("/proc/self/ns/pid")}


def main():
    probe = isolation()
    print(json.dumps({"ready": probe}), flush=True)
    line = sys.stdin.buffer.readline(2 * FILE_MAX + 1)
    if len(line) > 2 * FILE_MAX:
        raise ValueError("request limit")
    request = json.loads(line)
    root = os.open("/work", FLAGS | os.O_DIRECTORY)
    try:
        if request == {"op": "probe", "args": {}}:
            result = {"ok": True, "probe": probe}
        else:
            result = {"ok": True, "result": execute(root, request)}
    except (OSError, ValueError, UnicodeError) as e:
        result = {"ok": False, "error": str(e)[:500]}
    finally:
        os.close(root)
    data = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if len(data.encode()) > OUTPUT_MAX:
        data = json.dumps({"ok": False, "error": "result exceeds 64 KiB; narrow the request"})
    print(data, flush=True)


if __name__ == "__main__":
    main()
