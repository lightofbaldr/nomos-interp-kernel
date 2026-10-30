import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.mojo_tasks import Task
from harness2.core.muse_judge import MuseJudge, Refusal, key_from_file, meta_transport, validate

KEY = "fake-meta-only-for-offline-tests"
VERDICT = {
    "scores": {"readability": 3, "idiomatic_mojo": 2, "robustness": 1},
    "rationale": "Clear, but narrow.",
}


def response(obj=VERDICT, **kw):
    return json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(obj)}, **kw}]}
    ).encode()


class Judge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / "fake.env"
        self.key.write_text("META_API_HACKATHON=" + KEY + "\n")
        self.calls = []
        self.task = Task("x", "write x", "run", "train", "1")

    def judge(self, cap=2, transport=None):
        def fake(key, body):
            self.assertEqual(key, KEY)
            self.assertNotIn(KEY.encode(), body)
            self.calls.append(body)
            return response()

        return MuseJudge(
            self.root / "calls.db",
            approved=True,
            max_calls=cap,
            key_file=self.key,
            _transport=transport or fake,
        )

    def test_disabled_never_reads_secret(self):
        with patch("harness2.core.muse_judge.key_from_file", side_effect=AssertionError):
            self.assertEqual(
                MuseJudge(self.root / "x").review(self.task, "x", {})["status"], "not_requested"
            )

    def test_only_correct_variable_and_no_symlink(self):
        self.key.write_text("META_API_HACKATOHON=old\n")
        with self.assertRaises(Refusal):
            key_from_file(self.key)
        link = self.root / "link"
        link.symlink_to(self.key)
        with self.assertRaises(Refusal):
            key_from_file(link)

    def test_quality_is_separate_and_capped_across_instances(self):
        first = self.judge(cap=1).review(self.task, "a", {"passed": False})
        self.assertEqual(first["status"], "scored")
        self.assertNotIn("passed", first)
        self.assertEqual(self.judge(cap=1).review(self.task, "a", {"passed": False}), first)
        # Same source and grading fields bind the same body; different verdict is a different request.
        self.assertEqual(self.judge(cap=1).review(self.task, "b", {})["status"], "budget_exhausted")
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_cap_and_reuse(self):
        with ThreadPoolExecutor(4) as pool:
            results = list(
                pool.map(lambda i: self.judge(cap=2).review(self.task, str(i), {}), range(5))
            )
        self.assertEqual(sum(r["status"] == "scored" for r in results), 2)
        self.assertEqual(len(self.calls), 2)

    def test_failed_call_is_not_retried(self):
        def failure(*args):
            self.calls.append(1)
            raise OSError("provider failed " + KEY)

        a = self.judge(transport=failure).review(self.task, "x", {})
        b = self.judge().review(self.task, "x", {})
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "provider_transport_error")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(KEY.encode(), (self.root / "calls.db").read_bytes())

    def test_key_in_input_and_echo_refused(self):
        self.assertEqual(self.judge().review(self.task, KEY, {})["status"], "credential_in_payload")
        self.assertFalse(self.calls)
        result = self.judge(transport=lambda *_: response(VERDICT | {"rationale": KEY})).review(
            self.task, "x", {}
        )
        self.assertEqual(result["status"], "credential_echo")
        self.assertNotIn(KEY.encode(), (self.root / "calls.db").read_bytes())

    def test_validation_negative_controls(self):
        self.assertEqual(validate(response(), KEY), VERDICT)
        for value in (True, -1, 5, float("nan"), "3"):
            with self.subTest(value=value), self.assertRaises(Refusal):
                validate(
                    response(VERDICT | {"scores": VERDICT["scores"] | {"readability": value}}), KEY
                )
        for raw in (
            b"not json",
            response(finish_reason="length"),
            response(VERDICT | {"extra": 1}),
            response(VERDICT | {"rationale": "word " * 121}),
        ):
            with self.assertRaises(Refusal):
                validate(raw, KEY)

    def test_cap_cannot_change_on_resume(self):
        self.judge(cap=1).review(self.task, "x", {})
        with self.assertRaises(ValueError):
            self.judge(cap=2).review(self.task, "y", {})

    def test_actual_transport_against_fake_http_api_without_redirects(self):
        calls = []

        class FakeMeta(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(
                    (
                        self.path,
                        self.headers.get("Authorization"),
                        self.rfile.read(int(self.headers["Content-Length"])),
                    )
                )
                self.send_response(200 if len(calls) == 1 else 302)
                self.send_header("Location", "http://must-not-be-followed.invalid/")
                self.end_headers()
                self.wfile.write(response())

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMeta)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def local_transport(host, **kw):
                self.assertEqual(host, "api.meta.ai")
                self.assertEqual(kw["timeout"], 60)
                self.assertIn("context", kw)
                return http.client.HTTPConnection(*server.server_address, timeout=5)

            with patch("harness2.core.muse_judge.http.client.HTTPSConnection", local_transport):
                first = self.judge(transport=meta_transport).review(self.task, "one", {})
                second = self.judge(transport=meta_transport).review(self.task, "two", {})
            self.assertEqual(first["status"], "scored")
            self.assertEqual(second["status"], "provider_http_error")
            self.assertEqual(len(calls), 2)
            for path, authorization, body in calls:
                self.assertEqual(path, "/v1/chat/completions")
                self.assertEqual(authorization, "Bearer " + KEY)
                self.assertNotIn(KEY.encode(), body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_escaped_credential_echo_is_rejected(self):
        raw = response(VERDICT | {"rationale": KEY}).replace(b"fake-meta", b"\\u0066ake-meta")
        with self.assertRaisesRegex(Refusal, "credential_echo"):
            validate(raw, KEY)
