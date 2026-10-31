"""One ephemeral Unix socket, one fixed local model route; no credential handling.

The caller's isolated network has no host loopback. Only this socket is mounted.
This broker is NOT a general CONNECT proxy and never honors HTTP_PROXY or redirects.
"""

from __future__ import annotations

import http.client
import json
import socket
import socketserver
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MODEL = "nomos-muse-glimmer-30b"
MAX_BODY = 1_048_576


def error_detail(status, code="upstream_http", max_calls=32):
    """Only fixed diagnostics cross the boundary; no upstream body/headers/exception."""
    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError("invalid broker error status")
    if code == "model_call_cap" and status == 429:
        message = f"call cap reached at {max_calls}"
    elif code == "upstream_transport" and status == 502:
        message = "Upstream transport failed (HTTP 502); no automatic retry."
    elif code in ("upstream_http", "broker_rejected", "broker_closed"):
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = "Unrecognized status"
        prefix = "Upstream" if code == "upstream_http" else "Model broker"
        message = f"{prefix} returned HTTP {status} ({phrase})."
        if code == "upstream_http" and status == 413:
            message += " Request size or context limit exceeded."
    else:
        raise ValueError("invalid broker error code")
    return {"status": status, "code": code, "message": message}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class ModelBroker:
    def __init__(self, path: Path, *, max_calls=32, _connection=None):
        if type(max_calls) is not int or not 1 <= max_calls <= 128:
            raise ValueError("model call cap must be 1..128")
        self.calls = 0
        self.last_failure = None
        self.lock = threading.Lock()
        self.serial = threading.Lock()
        self.closed = False
        self.active = set()
        self.max_calls = max_calls
        # Dependency injection is used only by offline tests, never by a CLI flag/env.
        self.connection = _connection or (
            lambda: http.client.HTTPConnection("127.0.0.1", 8091, timeout=5)
        )
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(120)

            def reject(self, code, kind="broker_rejected"):
                detail = error_detail(code, kind, owner.max_calls)
                owner.last_failure = detail
                body = json.dumps(
                    {"error": {"type": "model_broker_error", **detail}}, allow_nan=False
                ).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_POST(self):
                if (
                    self.path != "/v1/chat/completions"
                    or self.headers.get("Transfer-Encoding") is not None
                    or len(self.headers.get_all("Content-Length", [])) != 1
                ):
                    return self.reject(400)
                try:
                    length = int(self.headers["Content-Length"])
                    if not 0 < length <= MAX_BODY:
                        return self.reject(413)
                    body = self.rfile.read(length)
                    if len(body) != length:
                        return self.reject(400)
                    req = json.loads(body)
                    if (
                        not isinstance(req, dict)
                        or req.get("model") != MODEL
                        or "nomos" in req
                        or not isinstance(req.get("messages"), list)
                    ):
                        return self.reject(400)
                    if not any(key in req for key in ("max_tokens", "max_completion_tokens")):
                        return self.reject(400)
                    for key in ("max_tokens", "max_completion_tokens"):
                        if key in req and (type(req[key]) is not int or not 1 <= req[key] <= 8192):
                            return self.reject(400)
                except (ValueError, OSError):
                    return self.reject(400)
                with owner.lock:
                    if owner.closed:
                        return self.reject(429, "broker_closed")
                    if owner.calls >= owner.max_calls:
                        return self.reject(429, "model_call_cap")
                    owner.calls += 1  # admitted failures count; no automatic retry
                conn = owner.connection()
                sent = False
                try:
                    with owner.serial:
                        # Closing the rollout revokes admission even for queued handlers.
                        # Request submission is bounded by the short connection timeout.
                        with owner.lock:
                            if owner.closed:
                                return self.reject(503, "broker_closed")
                            owner.active.add(conn)
                            conn.request(
                                "POST",
                                "/v1/chat/completions",
                                body=body,
                                headers={"Content-Type": "application/json"},
                            )
                            if conn.sock:
                                conn.sock.settimeout(120)
                        response = conn.getresponse()
                        if response.status != 200:
                            sent = True
                            return self.reject(response.status, "upstream_http")
                        self.send_response(200)
                        content_type = response.getheader("Content-Type", "application/json")
                        self.send_header(
                            "Content-Type",
                            "text/event-stream"
                            if "text/event-stream" in content_type
                            else "application/json",
                        )
                        self.end_headers()
                        sent = True
                        total = 0
                        while chunk := response.read1(65536):
                            total += len(chunk)
                            if total > 32 * 1024 * 1024:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    if not sent:
                        self.reject(502, "upstream_transport")
                finally:
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
