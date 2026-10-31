"""Explicit operator credentials only. No login files, discovery or refresh.

Subscription tokens are client-held and passed through, never broker-injected.
The native client shares its PID namespace with tools: env-only injection does
NOT protect that token against a tool reading /proc and copying it into scratch.
The subscription live route is therefore held; fake protocol tests are separate.
"""

import os
from dataclasses import dataclass, field, replace

MODES = ("subscription", "api-key")
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
class Credentials:
    mode: str
    secret: str = field(repr=False)

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError("choose subscription or api-key explicitly")
        check_secret(self.secret)

    @classmethod
    def from_env(cls, mode, env=None):
        env = os.environ if env is None else env
        if mode not in MODES:
            raise ValueError("choose subscription or api-key explicitly")
        key = "CLAUDE_CODE_OAUTH_TOKEN" if mode == "subscription" else "ANTHROPIC_API_KEY"
        return cls(mode, check_secret(env.get(key)))

    def broker_key(self):
        return self.secret if self.mode == "api-key" else None

    def client_input(self):
        # A bounded anonymous pipe, not systemd/bwrap argv, a file or a journal.
        return (self.secret if self.mode == "subscription" else PLACEHOLDER).encode("ascii")

    def scrub(self, result):
        # Literal redaction only. Encoded secrets/model-authored files are NOT
        # covered. Do not enable subscription on the strength of this helper.
        echoed = self.secret in result.stdout or self.secret in result.stderr
        return replace(
            result,
            stdout=result.stdout.replace(self.secret, "[REDACTED]"),
            stderr=result.stderr.replace(self.secret, "[REDACTED]"),
            reason="credential_echo" if echoed else result.reason,
        )
