import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_pi_runner import Runner, file_bytes
from harness2.benchmarks.mojo_tasks import Corpus, Task
from harness2.substrate.model_broker import MODEL, ModelBroker
from harness2.substrate.sandbox import Sandbox


class FakeModel:
    """A real local HTTP/SSE fake; it never contacts :8091 or any external service."""

    def __init__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(req)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                used_tool = any(m.get("role") == "tool" for m in req["messages"])
                delta = (
                    {"role": "assistant", "content": "Saved the program."}
                    if used_tool
                    else {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "offline-write-1",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": json.dumps(
                                        {
                                            "path": "/work/solution.mojo",
                                            "content": "def main():\n    print(7)\n",
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                )
                for choice in (
                    {"index": 0, "delta": delta, "finish_reason": None},
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if used_tool else "tool_calls",
                    },
                ):
                    data = {
                        "id": "offline",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": MODEL,
                        "choices": [choice],
                    }
                    self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(3)
        self.sock.connect(self.path)


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h2-offline-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()

    def test_real_namespace_files_env_caps_and_network(self):
        sentinel = self.root / "host-private.env"
        sentinel.write_text("sentinel-secret-not-a-real-key")
        code = f"""import os, pathlib, socket
assert not pathlib.Path({str(sentinel)!r}).exists()
assert not pathlib.Path({str(Path.home() / '.env')!r}).exists()
assert not pathlib.Path('/run/user/1000').exists()
assert 'META_API_HACKATHON' not in os.environ
assert 'HTTP_PROXY' not in os.environ
assert not pathlib.Path('/dev/nvidia0').exists()
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
for path in ['/usr/harness2-sentinel', '/etc/harness2-sentinel', '/outside']:
    try: pathlib.Path(path).write_text('no')
    except OSError: pass
    else: raise AssertionError(path)
for address in [('127.0.0.1',8091), ('1.1.1.1',80)]:
    try: socket.create_connection(address, timeout=.2)
    except OSError: pass
    else: raise AssertionError('unexpected network')
pathlib.Path('/work/result').write_text('yes')
pathlib.Path('/tmp/result').write_text('private')
print('ISOLATION VERIFIED')
"""
        with patch.dict(
            os.environ,
            {"META_API_HACKATHON": "fake-parent-key", "HTTP_PROXY": "http://localhost:1"},
        ):
            result = Sandbox().run(self.work, ["/usr/bin/python3", "-I", "-c", code], timeout=20)
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("ISOLATION VERIFIED", result.stdout)
        self.assertEqual((self.work / "result").read_text(), "yes")
        self.assertEqual((self.work / ".tmp/result").read_text(), "private")
        self.assertEqual(sentinel.read_text(), "sentinel-secret-not-a-real-key")

    def test_symlink_workspace_and_artifact_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(ValueError):
            Sandbox().argv(link, ["/usr/bin/true"])
        artifact = self.work / "solution.mojo"
        artifact.symlink_to(self.root / "other")
        with self.assertRaises(OSError):
            file_bytes(artifact)

    def test_output_limit_and_timeout_kill_service(self):
        result = Sandbox().run(
            self.work,
            ["/usr/bin/python3", "-I", "-c", "print('x'*200000)"],
            timeout=10,
            output_limit=1024,
        )
        self.assertEqual(result.reason, "output_limit")
        result = Sandbox().run(
            self.work, ["/usr/bin/python3", "-I", "-c", "import time;time.sleep(10)"], timeout=1
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(result.seconds, 8)

    def test_broker_only_fixed_model_route_and_cap(self):
        with (
            FakeModel() as fake,
            ModelBroker(
                self.root / "model.sock", max_calls=1, _connection=fake.connection
            ) as broker,
        ):

            def request(path, data):
                # A header-only refusal can legitimately close before a second
                # body send. Send this small status fixture in one write, as in
                # test_improver_broker; keep the production refusal unchanged.
                body = json.dumps(data).encode()
                head = (f"POST {path} HTTP/1.0\r\nContent-Length: {len(body)}\r\n"
                        "Content-Type: application/json\r\n\r\n").encode()
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(3)
                    conn.connect(str(self.root / "model.sock"))
                    conn.sendall(head + body)
                    response = http.client.HTTPResponse(conn)
                    response.begin()
                    response.read()
                    return response.status

            body = {"model": MODEL, "messages": [], "max_tokens": 32}
            for path, data in (
                ("/admin", body),
                ("http://evil/v1/chat/completions", body),
                ("/v1/chat/completions", body | {"model": "paid-other"}),
                ("/v1/chat/completions", body | {"nomos": {}}),
                ("/v1/chat/completions", {"model": MODEL, "messages": []}),
            ):
                self.assertEqual(request(path, data), 400)
            self.assertEqual(request("/v1/chat/completions", body), 200)
            self.assertEqual(request("/v1/chat/completions", body), 429)
            self.assertEqual(broker.calls, 1)
        self.assertEqual(len(fake.requests), 1)

    def test_closed_broker_refuses_new_admission(self):
        broker = ModelBroker(
            self.root / "model.sock",
            max_calls=1,
            _connection=lambda: self.fail("request escaped closed admission"),
        )
        with broker:
            with broker.lock:
                broker.closed = True
            with closing(UnixConnection(self.root / "model.sock")) as conn:
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    json.dumps({"model": MODEL, "messages": [], "max_tokens": 32}),
                )
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 429)
            self.assertEqual(broker.calls, 0)

    def test_closing_broker_revokes_already_admitted_waiting_request(self):
        statuses = []
        with FakeModel() as fake:
            broker = ModelBroker(self.root / "queued.sock", _connection=fake.connection)
            broker.serial.acquire()

            def request():
                with closing(UnixConnection(self.root / "queued.sock")) as conn:
                    conn.request(
                        "POST",
                        "/v1/chat/completions",
                        json.dumps({"model": MODEL, "messages": [], "max_tokens": 32}),
                    )
                    response = conn.getresponse()
                    response.read()
                    statuses.append(response.status)

            thread = threading.Thread(target=request, daemon=True)
            try:
                with broker:
                    thread.start()
                    deadline = time.monotonic() + 2
                    while broker.calls != 1 and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(broker.calls, 1)
                    self.assertEqual(fake.requests, [])
                # Context exit has revoked the admitted request before releasing it.
            finally:
                broker.serial.release()
                thread.join(timeout=3)
            self.assertEqual(statuses, [503])
            self.assertEqual(fake.requests, [])

    def test_workspace_watchdog_stops_excess_writes(self):
        with patch("harness2.substrate.sandbox.WORKSPACE_BYTES", 1024):
            result = Sandbox().run(
                self.work,
                [
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "from pathlib import Path; import time; Path('large').write_bytes(b'x'*2048); time.sleep(5)",
                ],
                timeout=10,
            )
        self.assertEqual(result.reason, "workspace_limit", result)
        self.assertLess(result.seconds, 5)

    def test_real_pi_fake_model_write_and_completed_resume(self):
        train, test = self.root / "train", self.root / "held"
        train.write_text(
            json.dumps(
                {
                    "id": "x",
                    "kind": "run",
                    "prompt": "Write a program printing 7",
                    "expect_stdout": "7",
                }
            )
        )
        test.write_text(
            json.dumps(
                {
                    "id": "y",
                    "kind": "run",
                    "prompt": "Write a program printing 9",
                    "expect_stdout": "9",
                }
            )
        )
        adapter = MojoAdapter(corpus=Corpus(train, test), runs_root=self.root / "runs")
        sandbox = adapter.runtime()
        with FakeModel() as fake:

            def factory(path, **kw):
                return ModelBroker(path, **kw, _connection=fake.connection)

            runner = Runner(sandbox, approved=True, _broker=factory)
            run = self.root / "rollout"
            runner.run(adapter.corpus.tasks["x"], adapter.base_harness(), run, timeout=30)
            self.assertEqual((run / "solution.mojo").read_text(), "def main():\n    print(7)\n")
            self.assertEqual(len(fake.requests), 2)
            runner.run(adapter.corpus.tasks["x"], adapter.base_harness(), run, timeout=30)
            self.assertEqual(len(fake.requests), 2)
            (run / "solution.mojo").write_text("changed")
            with self.assertRaises(ValueError):
                runner.run(adapter.corpus.tasks["x"], adapter.base_harness(), run, timeout=30)

    def test_runner_without_approval_cannot_start_sandbox(self):
        with patch.object(Sandbox, "run", side_effect=AssertionError("called")):
            with self.assertRaisesRegex(RuntimeError, "held"):
                Runner(Sandbox()).run(
                    Task("x", "x", "compile", "train"), self.work, self.root / "out"
                )
