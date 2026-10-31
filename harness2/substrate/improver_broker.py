"""Fixed Messages gateway: subscription pass-through OR operator API-key injection.

Subscription has no configured broker credential: each request carries the native
client's own Authorization, forwarded unchanged along with its OAuth beta header.
API-key mode replaces an inert client key with the operator's host-held key.
No login-file reader, refresh, alternate provider, redirect or proxy environment.
"""

import http.client
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler

from .improver_auth import MODES, PLACEHOLDER, check_secret
from .model_broker import _Server

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
    def __init__(self, path, *, mode, model, api_key=None, max_calls=MAX_CALLS, _connection=None):
        if mode not in MODES or (mode == "subscription" and api_key is not None):
            raise ValueError("invalid broker authentication mode")
        if mode == "api-key":
            check_secret(api_key)
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
                # Duplicate or mixed auth cannot choose a different credential.
                # A subscription request's token is local to this handler, never
                # configured on the broker or written to its diagnostics.
                try:
                    if mode == "subscription":
                        values = self.headers.get_all("Authorization", [])
                        if (
                            len(values) != 1
                            or not values[0].startswith("Bearer ")
                            or self.headers.get_all("x-api-key")
                        ):
                            raise ValueError
                        secret = check_secret(values[0][7:])
                        auth = {"Authorization": values[0]}
                    else:
                        if self.headers.get_all("x-api-key") != [
                            PLACEHOLDER
                        ] or self.headers.get_all("Authorization"):
                            raise ValueError
                        secret = api_key
                        auth = {"x-api-key": api_key}
                except ValueError:
                    return self.refuse(403, "authentication does not match selected mode")
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
                        **auth,
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
                    if mode == "subscription" and not any(
                        re.fullmatch(r"oauth-\d{4}-\d{2}-\d{2}", part.strip())
                        for part in headers.get("anthropic-beta", "").split(",")
                    ):
                        raise ValueError
                except (ValueError, TypeError, OSError):
                    return self.refuse(400, "invalid or over-budget model request")
                with owner.lock:
                    if owner.closed:
                        return self.refuse(503, "improver attempt closed")
                    if owner.calls >= owner.max_calls:
                        return self.refuse(429, "improver model call budget exhausted")
                    owner.calls += 1
                conn, sent = None, False
                try:
                    with owner.serial:
                        with owner.lock:
                            if owner.closed:
                                sent = True
                                return self.refuse(503, "improver attempt closed")
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
                                    and len(name) <= 128
                                    and re.fullmatch(r"[A-Za-z0-9-]+", name)
                                    and value.isascii()
                                    and all(32 <= ord(c) < 127 for c in value)
                                    and secret not in value
                                    and secret.lower() not in name.lower()
                                ):
                                    self.send_header(name, value)
                        self.end_headers()
                        sent = True
                        guard, total = _SecretGuard(secret), 0
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
