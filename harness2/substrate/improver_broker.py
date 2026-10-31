"""Fixed Anthropic Messages gateway. The OAuth credential never enters the sandbox.

No CONNECT, redirects, alternate providers, arbitrary paths or forwarded client
credentials. This gateway is for the actual Claude Code client, not an OAuth SDK
replacement. Production has no configurable upstream URL or proxy environment.
"""

import http.client
import json
import math
import os
import re
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .model_broker import _Server

PLACEHOLDER = "harness2-inert-oauth-placeholder"
MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
MAX_CALLS = 128
MAX_TOKENS = 32768
PATHS = {
    "/v1/messages",
    "/v1/messages?beta=true",
    "/v1/messages/count_tokens",
    "/v1/messages/count_tokens?beta=true",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Login:
    token: str = field(repr=False)
    expires_at: float

    def __post_init__(self):
        if (
            not isinstance(self.token, str)
            or not 16 <= len(self.token) <= 4096
            or not self.token.isascii()
            or any(ord(c) <= 32 or ord(c) >= 127 for c in self.token)
            or type(self.expires_at) not in (int, float)
            or not math.isfinite(self.expires_at)
        ):
            raise ValueError("invalid OAuth credential")


def load_login(path, *, min_ttl=0):
    """Read the Linux login on the host, with no refresh, write or env-key fallback."""
    path = Path(path)
    if path.absolute() != path.resolve():
        raise ValueError("linked login file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise ValueError("login must be a private owner-only regular file")
        with os.fdopen(fd, "rb", closefd=False) as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized login file")
        try:
            obj = json.loads(raw)["claudeAiOauth"]
            expiry = obj["expiresAt"]
            if type(expiry) not in (int, float) or not math.isfinite(expiry):
                raise ValueError
            login = Login(obj["accessToken"], expiry / 1000)
            if not isinstance(obj.get("scopes"), list) or "user:inference" not in obj["scopes"]:
                raise ValueError
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
            raise ValueError("invalid login credential or scope/expiry") from None
        if login.expires_at <= time.time() + min_ttl:
            raise ValueError(
                "login expired or expiry does not cover this attempt; refresh outside the run"
            )
        return login
    finally:
        os.close(fd)


def model_allowed(requested, configured):
    if not isinstance(requested, str):
        return False
    if configured in ("opus", "sonnet", "haiku"):
        return re.fullmatch(r"claude-" + configured + r"-[a-z0-9-]{1,80}", requested) is not None
    return (
        requested == configured and re.fullmatch(r"claude-[a-z0-9-]{1,96}", requested) is not None
    )


class _SecretGuard:
    def __init__(self, token):
        self.secret, self.tail = token.encode(), b""

    def feed(self, data, final=False):
        data = self.tail + data
        if self.secret in data:
            raise ValueError("credential echo refused")
        cut = len(data) if final else max(0, len(data) - len(self.secret) + 1)
        out, self.tail = data[:cut], data[cut:]
        return out


class ApiBroker:
    def __init__(self, path, login, *, model, max_calls=MAX_CALLS, _connection=None):
        if type(max_calls) is not int or not 1 <= max_calls <= MAX_CALLS:
            raise ValueError("invalid improver call budget")
        if not (model in ("opus", "sonnet", "haiku") or model_allowed(model, model)):
            raise ValueError("unsupported Claude model identifier")
        self.calls, self.max_calls, self.closed = 0, max_calls, False
        self.lock, self.serial = threading.Lock(), threading.Lock()
        self.active = set()
        self.last_error = None
        self.connection = _connection or (
            lambda: http.client.HTTPSConnection("api.anthropic.com", 443, timeout=5)
        )
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(30)

            def refuse(self, status, reason):
                owner.last_error = {"status": status, "reason": reason}
                body = json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "type": "rate_limit_error" if status == 429 else "api_error",
                            "message": reason,
                        },
                    },
                    allow_nan=False,
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_CONNECT(self):
                self.refuse(405, "CONNECT is not allowed")

            def do_GET(self):
                self.refuse(405, "only model POST requests are allowed")

            def do_HEAD(self):
                self.send_response(204 if self.path == "/api/hello" else 404)
                self.end_headers()
                self.close_connection = True

            def do_POST(self):
                if self.path not in PATHS:
                    return self.refuse(404, "model API path is not allowed")
                if self.headers.get_all("Authorization") != ["Bearer " + PLACEHOLDER]:
                    return self.refuse(403, "placeholder authentication required")
                if (
                    self.headers.get("Transfer-Encoding") is not None
                    or len(self.headers.get_all("Content-Length", [])) != 1
                ):
                    return self.refuse(400, "invalid framing")
                try:
                    size = int(self.headers["Content-Length"])
                    if not 0 < size <= MAX_BODY:
                        return self.refuse(413, "improver request byte limit")
                    body = self.rfile.read(size)
                    if len(body) != size:
                        raise ValueError
                    req = json.loads(
                        body,
                        object_pairs_hook=_unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if (
                        not isinstance(req, dict)
                        or not model_allowed(req.get("model"), model)
                        or not isinstance(req.get("messages"), list)
                    ):
                        raise ValueError
                    if self.path.partition("?")[0] == "/v1/messages" and (
                        type(req.get("max_tokens")) is not int
                        or not 1 <= req["max_tokens"] <= MAX_TOKENS
                    ):
                        raise ValueError
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + login.token,
                    }
                    for name in self.headers.keys():
                        lower = name.lower()
                        if lower.startswith("anthropic-") or lower == "user-agent":
                            values = self.headers.get_all(name)
                            if (
                                len(values) != 1
                                or len(values[0]) > 4096
                                or not values[0].isascii()
                                or any(ord(c) < 32 or ord(c) >= 127 for c in values[0])
                            ):
                                raise ValueError
                            headers[lower] = values[0]
                    if headers.get("anthropic-version") != "2023-06-01":
                        raise ValueError
                except (ValueError, TypeError, OSError):
                    return self.refuse(400, "invalid or over-budget model request")
                with owner.lock:
                    if owner.closed:
                        return self.refuse(503, "improver attempt closed")
                    if owner.calls >= owner.max_calls:
                        return self.refuse(429, "improver model call budget exhausted")
                    if login.expires_at <= time.time():
                        return self.refuse(401, "host login expired; no credential fallback")
                    owner.calls += 1
                conn, sent = None, False
                try:
                    with owner.serial:
                        with owner.lock:
                            if owner.closed:
                                sent = True
                                return self.refuse(503, "improver attempt closed")
                            if login.expires_at <= time.time():
                                sent = True
                                return self.refuse(
                                    401, "host login expired; no credential fallback"
                                )
                            conn = owner.connection()
                            owner.active.add(conn)
                            conn.request("POST", self.path, body=body, headers=headers)
                            if conn.sock:
                                conn.sock.settimeout(120)
                        response = conn.getresponse()
                        if response.status != 200:
                            sent = True
                            reason = (
                                "Upstream model API rate or usage limit (HTTP 429)"
                                if response.status == 429
                                else f"Upstream model API HTTP {response.status}; response detail withheld"
                            )
                            return self.refuse(response.status, reason)
                        self.send_response(200)
                        self.send_header(
                            "Content-Type",
                            "text/event-stream"
                            if "text/event-stream" in response.getheader("Content-Type", "")
                            else "application/json",
                        )
                        for name, value in response.getheaders():
                            if name.lower().startswith("anthropic-ratelimit-unified-"):
                                if (
                                    len(value) <= 512
                                    and value.isascii()
                                    and all(32 <= ord(c) < 127 for c in value)
                                    and login.token not in value
                                ):
                                    self.send_header(name, value)
                        self.end_headers()
                        sent = True
                        guard, total = _SecretGuard(login.token), 0
                        while chunk := response.read1(65536):
                            total += len(chunk)
                            if total > MAX_RESPONSE:
                                raise ValueError("improver response byte limit")
                            self.wfile.write(guard.feed(chunk))
                            self.wfile.flush()
                        self.wfile.write(guard.feed(b"", final=True))
                        self.wfile.flush()
                except (OSError, http.client.HTTPException, ValueError):
                    owner.last_error = {
                        "status": 502,
                        "reason": "transport or response safety failure",
                    }
                    if not sent:
                        self.refuse(502, "model transport or response safety failure")
                finally:
                    if conn:
                        with owner.lock:
                            owner.active.discard(conn)
                        conn.close()
                    self.close_connection = True

        self.server = _Server(str(path), Handler)
        path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        with self.lock:
            self.closed = True
            for conn in self.active:
                if conn.sock:
                    try:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                conn.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
