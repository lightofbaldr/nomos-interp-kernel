"""Fixed-route broker errors over local fixtures only; never host :8091."""

import http.client
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from harness2.substrate.model_broker import MODEL, ModelBroker
from scripts.check_mojo_followups_offline import guarded_connect


class OfflineGuard(unittest.TestCase):
    def test_live_provider_and_serve_addresses_never_connect(self):
        original = Mock(side_effect=AssertionError("network was reached"))
        connect = guarded_connect(original)
        for family, address in (
            (socket.AF_INET, ("127.0.0.1", 8091)),
            (socket.AF_INET6, ("::1", 8091)),
            (socket.AF_INET, ("api.example.invalid", 443)),
        ):
            with (
                self.subTest(address=address),
                self.assertRaisesRegex(AssertionError, "Offline regression"),
            ):
                connect(Mock(family=family), address)
        original.assert_not_called()

    def test_local_fixture_and_unix_connections_are_retained(self):
        original = Mock(return_value=0)
        connect = guarded_connect(original)
        for family, address in (
            (socket.AF_INET, ("127.0.0.1", 48123)),
            (socket.AF_UNIX, "/temporary-fixture.sock"),
        ):
            self.assertEqual(connect(Mock(family=family), address), 0)
        self.assertEqual(original.call_count, 2)


class BrokerErrors(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-broker-errors-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "broker.sock"

    def request(self, body=None, path="/v1/chat/completions"):
        # A bad route can be rejected before the broker reads its body. Send
        # this small fixture in one write: http.client's separate header/body
        # sends race that valid early close and can raise BrokenPipeError.
        raw = json.dumps(body or {"model": MODEL, "messages": [], "max_tokens": 32}).encode()
        head = f"POST {path} HTTP/1.0\r\nContent-Length: {len(raw)}\r\n\r\n".encode()
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(3)
            conn.connect(str(self.path))
            conn.sendall(head + raw)
            response = http.client.HTTPResponse(conn)
            response.begin()
            return response.status, dict(response.getheaders()), response.read()

    def upstream(self, status):
        response = Mock(status=status)
        response.read = Mock(side_effect=AssertionError("Do not echo arbitrary upstream bodies"))
        response.read1 = Mock(side_effect=AssertionError("Do not echo arbitrary upstream bodies"))
        response.getheader.return_value = "Bearer secret; Set-Cookie=private"
        conn = Mock(sock=None)
        conn.getresponse.return_value = response
        return conn

    def test_upstream_status_is_preserved_with_non_secret_bounded_json(self):
        for status in (400, 401, 403, 413, 429, 500, 503, 307):
            with self.subTest(status=status):
                conn = self.upstream(status)
                with ModelBroker(self.path, _connection=lambda: conn) as broker:
                    actual, headers, body = self.request()
                    self.assertEqual(actual, status)
                    self.assertEqual(headers["Content-Type"], "application/json")
                    error = json.loads(body)["error"]
                    self.assertIn(str(status), error["message"])
                    self.assertLess(len(body), 1024)
                    self.assertNotIn(b"secret", body)
                    self.assertNotIn("Location", headers)
                    self.assertEqual(broker.last_failure["status"], status)
                    self.assertEqual(broker.calls, 1)
                self.path.unlink()

    def test_call_cap_has_an_explicit_reason_and_no_extra_forward(self):
        conn = self.upstream(413)
        with ModelBroker(self.path, max_calls=1, _connection=lambda: conn) as broker:
            self.assertEqual(self.request()[0], 413)
            status, _, body = self.request()
            self.assertEqual(status, 429)
            self.assertIn("call cap reached at 1", json.loads(body)["error"]["message"])
            self.assertEqual(broker.calls, 1)
            self.assertEqual(conn.request.call_count, 1)

    def test_transport_failure_is_bounded_502_without_exception_text(self):
        conn = Mock(sock=None)
        conn.getresponse.side_effect = http.client.HTTPException("Bearer private-secret")
        with ModelBroker(self.path, _connection=lambda: conn):
            status, _, body = self.request()
        self.assertEqual(status, 502)
        self.assertNotIn(b"private-secret", body)
        self.assertIn("upstream", json.loads(body)["error"]["message"].lower())

    def test_route_model_and_nomos_rejections_never_reach_upstream(self):
        with ModelBroker(self.path, _connection=lambda: self.fail("forwarded")) as broker:
            self.assertEqual(self.request(path="/other")[0], 400)
            self.assertEqual(
                self.request({"model": "paid", "messages": [], "max_tokens": 32})[0], 400
            )
            self.assertEqual(
                self.request({"model": MODEL, "messages": [], "max_tokens": 32, "nomos": {}})[0],
                400,
            )
            self.assertEqual(broker.calls, 0)
