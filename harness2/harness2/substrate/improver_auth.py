"""Dedicated operator key file only; never inherit a parent client's API key."""

import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path

PLACEHOLDER = "harness2-inert-api-placeholder"
KEY_FILE_ENV = "HARNESS2_IMPROVER_API_KEY_FILE"
MAX_KEY_FILE = 4098  # 4096 ASCII key bytes plus at most a CRLF.


def check_secret(value):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 4096
        or not value.isascii()
        or any(not 32 < ord(c) < 127 for c in value)
    ):
        raise ValueError("missing or invalid operator credential")
    return value


def _read_key_file(source):
    """One bounded descriptor read, with no symlink traversal or secret diagnostics.

    Provision outside sandbox-mounted paths; operator files must stay quiescent.
    The checks detect ordinary replacement/permission changes, not a hostile host.
    """
    try:
        if not isinstance(source, str) or not source or "\0" in source:
            raise ValueError
        path = Path(source)
        if not path.is_absolute() or len(path.parts) < 2 or ".." in path.parts:
            raise ValueError
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent = os.open(path.anchor, flags | os.O_DIRECTORY)
        try:
            for name in path.parts[1:-1]:
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_uid != os.geteuid()
                    or before.st_nlink != 1
                    or not 16 <= before.st_size <= MAX_KEY_FILE
                ):
                    raise ValueError
                with os.fdopen(fd, "rb", closefd=False) as file:
                    data = file.read(MAX_KEY_FILE + 1)
                after = os.fstat(fd)
                fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if len(data) != before.st_size or any(
                    getattr(before, field) != getattr(after, field) for field in fields
                ):
                    raise ValueError
            finally:
                os.close(fd)
        finally:
            os.close(parent)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]
        return check_secret(data.decode("ascii"))
    except (OSError, ValueError):
        raise ValueError(
            f"{KEY_FILE_ENV} must name an owned 0600 single-link regular credential file "
            "at an absolute non-symlink path, containing one 16..4096-byte ASCII key"
        ) from None


@dataclass(frozen=True)
class ApiCredential:
    secret: str = field(repr=False)

    def __post_init__(self):
        check_secret(self.secret)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        # Never inspect ANTHROPIC_API_KEY: a parent Claude session may own it.
        return cls(_read_key_file(env.get(KEY_FILE_ENV)))

    def scrub(self, result):
        # Literal redaction only. Encoded secrets/model-authored files are NOT
        # covered. The key is never supplied to the sandboxed client/tools.
        echoed = self.secret in result.stdout or self.secret in result.stderr
        return replace(
            result,
            stdout=result.stdout.replace(self.secret, "[REDACTED]"),
            stderr=result.stderr.replace(self.secret, "[REDACTED]"),
            reason="credential_echo" if echoed else result.reason,
        )
