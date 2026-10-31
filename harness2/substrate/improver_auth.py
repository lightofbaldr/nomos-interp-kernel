"""Explicit host-held API key only. No login discovery, pass-through or refresh."""

import os
from dataclasses import dataclass, field, replace

PLACEHOLDER = "harness2-inert-api-placeholder"


def check_secret(value):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 4096
        or not value.isascii()
        or any(not 32 < ord(c) < 127 for c in value)
    ):
        raise ValueError("missing or invalid operator credential")
    return value


@dataclass(frozen=True)
class ApiCredential:
    secret: str = field(repr=False)

    def __post_init__(self):
        check_secret(self.secret)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(check_secret(env.get("ANTHROPIC_API_KEY")))

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
