"""Credential and API boundary controls, exclusively fake credentials/connections."""

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

TOKEN = "FAKE-OPERATOR-API-KEY-DO-NOT-USE"


class Broker(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-improver-broker-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "broker.sock"

    def upstream(self, status=200, chunks=None):
        response = Mock(status=status)
        response.getheader.side_effect = lambda name, default=None: (
            "text/event-stream" if name.lower() == "content-type" else default
        )
        response.getheaders.return_value = [
            ("content-type", "text/event-stream"),
            ("set-cookie", TOKEN),
            ("location", "https://elsewhere.invalid/"),
            ("anthropic-ratelimit-unified-status", "allowed"),
        ]
        response.read1.side_effect = list(chunks or [b'data: {"type":"message_stop"}\n\n']) + [b""]
        response.read.side_effect = AssertionError("do not read arbitrary error bodies")
        conn = Mock(sock=None)
        conn.getresponse.return_value = response
        return conn

    def request(
        self, method="POST", path="/v1/messages?beta=true", body=None, headers=None, raw_body=None
    ):
        from harness2.substrate.improver_broker import PLACEHOLDER

        payload = json.dumps(
            body
            if body is not None
            else {"model": "claude-opus-4-6", "max_tokens": 1024, "messages": []}
        )
        h = {
            "x-api-key": PLACEHOLDER,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20,fixture-new-capability",
            **(headers or {}),
        }
        # Send the small fixture atomically: a fail-fast HTTP/1.0 refusal may
        # close before http.client's separate body send, which is a valid refusal
        # but not the status oracle this test is exercising.
        raw = payload.encode() if raw_body is None else raw_body
        head = f"{method} {path} HTTP/1.0\r\nContent-Length: {len(raw)}\r\n"
        head += (
            "".join(f"{key}: {value}\r\n" for key, value in h.items() if value is not None) + "\r\n"
        )
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(self.path))
            client.sendall(head.encode() + raw)
            response = http.client.HTTPResponse(client)
            response.begin()
            return response.status, dict(response.getheaders()), response.read()

    def test_fixed_api_key_route_forwards_body_and_beta_but_injects_operator_key(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream()
        with ApiBroker(
            self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
        ) as broker:
            status, headers, body = self.request()
            self.assertEqual(status, 200)
            self.assertIn(b"message_stop", body)
            self.assertNotIn(TOKEN.encode(), body)
            self.assertNotIn("Set-Cookie", headers)
            self.assertNotIn("Location", headers)
            self.assertEqual(broker.calls, 1)
        args, kw = conn.request.call_args
        self.assertEqual(args, ("POST", "/v1/messages?beta=true"))
        self.assertEqual(json.loads(kw["body"])["model"], "claude-opus-4-6")
        self.assertEqual(kw["headers"]["x-api-key"], TOKEN)
        self.assertEqual(kw["headers"]["anthropic-beta"], "oauth-2025-04-20,fixture-new-capability")
        self.assertNotIn("Authorization", kw["headers"])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_routes_models_tokens_and_auth_are_rejected_before_upstream(self):
        from harness2.substrate.improver_broker import ApiBroker

        with ApiBroker(
            self.path,
            mode="api-key",
            api_key=TOKEN,
            model="opus",
            _connection=lambda: self.fail("unexpected forwarding"),
        ) as broker:
            for method, path in (
                ("CONNECT", "api.anthropic.com:443"),
                ("GET", "/v1/models"),
                ("POST", "https://other.invalid/v1/messages"),
                ("POST", "/v1/messages?target=elsewhere"),
                ("POST", "/v1/../oauth/token"),
                ("POST", "/v1/messages%2f"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertIn(self.request(method, path)[0], (400, 404, 405))
            for change in (
                {"model": "paid-other"},
                {"model": "claude-haiku-4-5"},
                {"max_tokens": True},
                {"max_tokens": 1000000},
                {"messages": "x"},
            ):
                self.assertEqual(
                    self.request(
                        body={
                            "model": "claude-opus-4-6",
                            "max_tokens": 1024,
                            "messages": [],
                            **change,
                        }
                    )[0],
                    400,
                )
            self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 403)
            duplicate = b'{"model":"claude-haiku-4-5","model":"claude-opus-4-6","max_tokens":1,"messages":[]}'
            self.assertEqual(self.request(raw_body=duplicate)[0], 400)
            self.assertEqual(
                self.request(
                    raw_body=b'{"model":"claude-opus-4-6","max_tokens":NaN,"messages":[]}'
                )[0],
                400,
            )
            self.assertEqual(broker.calls, 0)

    def test_default_destination_ignores_parent_url_and_proxy(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream()
        with (
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
                    "HTTPS_PROXY": "http://elsewhere.invalid",
                },
            ),
            patch(
                "harness2.substrate.improver_broker.http.client.HTTPSConnection", return_value=conn
            ) as factory,
            ApiBroker(self.path, mode="api-key", api_key=TOKEN, model="opus"),
        ):
            self.assertEqual(self.request()[0], 200)
        factory.assert_called_once_with("api.anthropic.com", 443, timeout=5)

    def test_successful_response_is_size_bounded(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream(chunks=[b"x" * 65])
        with (
            patch("harness2.substrate.improver_broker.MAX_RESPONSE", 64),
            ApiBroker(
                self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
            ) as broker,
        ):
            status, _, body = self.request()
            self.assertEqual(status, 200)  # headers already sent; the unsafe stream is truncated
            self.assertEqual(body, b"")
            self.assertEqual(broker.last_error["status"], 502)

    def test_count_tokens_is_bounded_and_cap_and_closing_revoke_admission(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream()
        with ApiBroker(
            self.path,
            mode="api-key",
            api_key=TOKEN,
            model="opus",
            max_calls=1,
            _connection=lambda: conn,
        ) as broker:
            self.assertEqual(
                self.request(
                    path="/v1/messages/count_tokens",
                    body={"model": "claude-opus-4-6", "messages": []},
                )[0],
                200,
            )
            self.assertEqual(self.request()[0], 429)
            self.assertEqual(conn.request.call_count, 1)
            self.assertEqual(broker.calls, 1)
            broker.closed = True
            self.assertEqual(self.request()[0], 503)

    def test_close_revokes_an_already_admitted_queued_request(self):
        from harness2.substrate.improver_broker import ApiBroker

        results = []
        with ApiBroker(
            self.path,
            mode="api-key",
            api_key=TOKEN,
            model="opus",
            _connection=lambda: self.fail("queued request escaped"),
        ) as broker:
            broker.serial.acquire()
            thread = threading.Thread(target=lambda: results.append(self.request()[0]))
            thread.start()
            try:
                deadline = time.monotonic() + 2
                while broker.calls == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(broker.calls, 1)
                with broker.lock:
                    broker.closed = True
            finally:
                broker.serial.release()
                thread.join(timeout=3)
            self.assertEqual(results, [503])

    def test_status_errors_never_echo_private_body_headers_or_exception(self):
        from harness2.substrate.improver_broker import ApiBroker

        for status in (401, 413, 429, 503):
            conn = self.upstream(status)
            with ApiBroker(
                self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
            ):
                actual, headers, body = self.request()
                self.assertEqual(actual, status)
                self.assertNotIn(TOKEN.encode(), body)
                self.assertLess(len(body), 1024)
                self.assertNotIn("Location", headers)
            self.path.unlink()
        conn = self.upstream()
        conn.getresponse.side_effect = http.client.HTTPException(TOKEN)
        with ApiBroker(
            self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
        ):
            status, _, body = self.request()
            self.assertEqual(status, 502)
            self.assertNotIn(TOKEN.encode(), body)

    def test_a_token_echo_split_across_stream_chunks_is_not_released(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream(chunks=[b"data: ", TOKEN[:8].encode(), TOKEN[8:].encode(), b"\n\n"])
        with ApiBroker(
            self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
        ):
            _, _, body = self.request()
            self.assertNotIn(TOKEN.encode(), body)
            self.assertNotIn(TOKEN[:8].encode(), body)

    def test_response_headers_cannot_echo_operator_key_in_name_or_value(self):
        from harness2.substrate.improver_broker import ApiBroker

        conn = self.upstream()
        conn.getresponse.return_value.getheaders.return_value = [
            ("anthropic-ratelimit-unified-" + TOKEN, "allowed"),
            ("anthropic-ratelimit-unified-status", TOKEN),
        ]
        with ApiBroker(
            self.path, mode="api-key", api_key=TOKEN, model="opus", _connection=lambda: conn
        ):
            status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN, repr(headers))
        self.assertNotIn(TOKEN.encode(), body)

    def test_duplicate_authentication_is_rejected_in_both_modes(self):
        from harness2.substrate.improver_broker import PLACEHOLDER, ApiBroker

        for mode in ("subscription", "api-key"):
            with ApiBroker(
                self.path,
                mode=mode,
                api_key=TOKEN if mode == "api-key" else None,
                model="opus",
                _connection=lambda: self.fail("duplicate auth forwarded"),
            ):
                if mode == "subscription":
                    h = {
                        "x-api-key": None,
                        "Authorization": "Bearer " + TOKEN + "\r\nAuthorization: Bearer " + TOKEN,
                    }
                else:
                    h = {"x-api-key": PLACEHOLDER + "\r\nx-api-key: " + PLACEHOLDER}
                self.assertEqual(self.request(headers=h)[0], 403)
            self.path.unlink()

    def test_subscription_has_no_configured_credential_and_passes_each_clients_headers(self):
        from harness2.substrate.improver_broker import ApiBroker

        connections = []

        def connection():
            conn = self.upstream()
            connections.append(conn)
            return conn

        with ApiBroker(
            self.path, mode="subscription", model="opus", _connection=connection
        ) as broker:
            for token in ("FAKE-SETUP-TOKEN-ONE", "FAKE-SETUP-TOKEN-TWO"):
                status, _, _ = self.request(
                    headers={"x-api-key": None, "Authorization": "Bearer " + token}
                )
                self.assertEqual(status, 200)
                args, kw = connections[-1].request.call_args
                self.assertEqual(args, ("POST", "/v1/messages?beta=true"))
                self.assertEqual(kw["headers"]["Authorization"], "Bearer " + token)
                self.assertNotIn("x-api-key", kw["headers"])
                self.assertEqual(
                    kw["headers"]["anthropic-beta"], "oauth-2025-04-20,fixture-new-capability"
                )
                self.assertEqual(kw["headers"]["anthropic-version"], "2023-06-01")
                self.assertNotIn(token, repr(vars(broker)))
            self.assertEqual(broker.calls, 2)

    def test_subscription_refuses_mixed_auth_or_missing_oauth_capability(self):
        from harness2.substrate.improver_broker import ApiBroker

        with ApiBroker(
            self.path, mode="subscription", model="opus", _connection=lambda: self.fail("upstream")
        ):
            self.assertEqual(self.request()[0], 403)
            self.assertEqual(self.request(headers={"Authorization": "Bearer " + TOKEN})[0], 403)
            self.assertEqual(
                self.request(
                    headers={
                        "x-api-key": None,
                        "Authorization": "Bearer " + TOKEN,
                        "anthropic-beta": "not-oauth",
                    }
                )[0],
                400,
            )

    def test_broker_auth_configuration_is_explicit_and_validated_before_bind(self):
        from harness2.substrate.improver_broker import ApiBroker

        for mode, key in (
            ("subscription", TOKEN),
            ("off", None),
            ("api-key", None),
            ("api-key", TOKEN + "\n"),
        ):
            with self.subTest(mode=mode), self.assertRaises(ValueError) as err:
                ApiBroker(self.path, mode=mode, api_key=key, model="opus")
            self.assertNotIn(TOKEN, str(err.exception))
            self.assertFalse(self.path.exists())
