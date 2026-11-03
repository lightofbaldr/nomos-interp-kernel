"""Tiny real boundary canaries; no compiler, Pi, real login or paid invocation.

Separate from check_mojo_followups_offline.py. All credentials and replies are
fake; even the network canary deliberately avoids the reserved :8091 endpoint.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.substrate.improver import ImproverSandbox
from harness2.substrate.improver_auth import PLACEHOLDER
from harness2.substrate.improver_broker import ApiBroker


class Namespace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="h2-improver-ns-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work, self.controls = self.root / "work", self.root / "controls"
        self.work.mkdir()
        self.controls.mkdir()
        (self.controls / "settings.json").write_text("{}")
        (self.controls / "mission.txt").write_text("FAKE MISSION")
        self.sandbox = ImproverSandbox(
            claude=Path(sys.executable).resolve(), controls=self.controls
        )

    def run_code(self, code, **kwargs):
        return self.sandbox.run(
            self.work, ["/usr/bin/python3", "-I", "-c", code], timeout=10, **kwargs
        )

    def test_private_files_devices_caps_and_environment(self):
        sentinel = self.root / "private"
        sentinel.write_text("FAKE PRIVATE OUTSIDE WORK")
        code = f"""import os, pathlib
assert not pathlib.Path({str(sentinel)!r}).exists()
for path in [{str(Path.home() / '.env')!r}, {str(Path.home() / '.claude')!r}, '/run/user/1000', '/dev/nvidia0']:
    assert not pathlib.Path(path).exists(), path
for key in ['CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_AUTH_TOKEN', 'HTTP_PROXY', 'CLAUDECODE']:
    assert key not in os.environ, key
assert os.environ['ANTHROPIC_API_KEY'] == {PLACEHOLDER!r}
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
for path in ['/usr/h2-canary', '/etc/h2-canary', '/outside', '/opt/claude/claude']:
    try: pathlib.Path(path).write_text('forbidden')
    except OSError: pass
    else: raise AssertionError(path)
pathlib.Path('/work/kept').write_text('allowed')
pathlib.Path('/tmp/private').write_text('scratch')
print('PRIVATE PROCESS VERIFIED')
"""
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "FAKE INHERITED",
                "ANTHROPIC_AUTH_TOKEN": "FAKE AUTH",
                "HTTP_PROXY": "http://fake",
                "CLAUDECODE": "1",
            },
        ):
            result = self.run_code(code)
        self.assertEqual(result.returncode, 0, result)
        self.assertIsNone(result.reason)
        self.assertIn("PRIVATE PROCESS VERIFIED", result.stdout)
        self.assertEqual((self.work / "kept").read_text(), "allowed")
        self.assertEqual(sentinel.read_text(), "FAKE PRIVATE OUTSIDE WORK")

    def test_no_host_loopback_or_external_network(self):
        # An ephemeral inert host listener, not the model serve.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            host_namespace = os.readlink("/proc/self/ns/net")
            code = f"""import os, socket
assert os.readlink('/proc/self/ns/net') != {host_namespace!r}
for address in [('127.0.0.1', {port}), ('192.0.2.1', 443)]:
    try: socket.create_connection(address, timeout=.1)
    except OSError: pass
    else: raise AssertionError('network escaped')
print('NETWORK VERIFIED')
"""
            result = self.run_code(code)
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("NETWORK VERIFIED", result.stdout)

    def test_fake_gateway_passes_only_model_request_and_read_only_mission(self):
        token = "FAKE-HOST-GATEWAY-CREDENTIAL-ONLY"
        reply = Mock(status=200)
        reply.getheader.return_value = "application/json"
        reply.getheaders.return_value = []
        reply.read1.side_effect = [b'{"fixture":"response"}', b""]
        conn = Mock(sock=None)
        conn.getresponse.return_value = reply
        with ApiBroker(
            self.controls / "api.sock",
            api_key=token,
            model="opus",
            _connection=lambda: conn,
        ) as broker:
            code = """import http.client, json, os, pathlib, sys
assert sys.stdin.read() == 'FAKE MISSION'
for path in ['/runtime/mission.txt', '/runtime/settings.json', '/runtime/improver-entry.py']:
    try: pathlib.Path(path).write_text('forbidden')
    except OSError: pass
    else: raise AssertionError(path)
h = {'x-api-key': os.environ['ANTHROPIC_API_KEY'],
     'anthropic-version': '2023-06-01'}
body = json.dumps({'model': 'claude-opus-4-6', 'max_tokens': 32, 'messages': []})
c = http.client.HTTPConnection('127.0.0.1', 8092, timeout=3)
c.request('POST', '/v1/messages', body, h)
r = c.getresponse()
assert r.status == 200 and json.loads(r.read()) == {'fixture': 'response'}
c.close()
c = http.client.HTTPConnection('127.0.0.1', 8092, timeout=3)
c.request('POST', '/oauth/token', body, h)
r = c.getresponse()
assert r.status == 404
r.read()
print('FAKE GATEWAY VERIFIED')
"""
            result = self.run_code(code, broker=self.controls / "api.sock")
            self.assertEqual(broker.calls, 1)
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("FAKE GATEWAY VERIFIED", result.stdout)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertEqual(conn.request.call_args.kwargs["headers"]["x-api-key"], token)
        self.assertEqual((self.controls / "mission.txt").read_text(), "FAKE MISSION")

    def test_output_limit_and_background_descendant_teardown(self):
        result = self.run_code("print('x' * 200000)", output_limit=1024)
        self.assertEqual(result.reason, "output_limit")
        code = """import subprocess, time
subprocess.Popen(['/usr/bin/python3', '-I', '-c',
                 "import time,pathlib;time.sleep(3);pathlib.Path('/work/escaped').write_text('bad')"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(20)
"""
        result = self.sandbox.run(self.work, ["/usr/bin/python3", "-I", "-c", code], timeout=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(result.seconds, 8)
        time.sleep(3.1)
        self.assertFalse((self.work / "escaped").exists())

    def test_native_api_key_and_bash_with_fake_injecting_gateway(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import captured_reply, prepare_client
        from harness2.substrate.improver_audit import AttemptAudit, load_attempt

        binary = shutil.which("claude")
        self.assertIsNotNone(binary, "native client control needs the installed Claude binary")
        sandbox = ImproverSandbox(claude=Path(binary), controls=self.controls)
        sentinel = self.root / "host-sentinel"
        sentinel.write_text("PRIVATE FAKE SENTINEL")
        token = "FAKE-NATIVE-CLIENT-OPERATOR-CREDENTIAL"
        probe = (
            "import pathlib,os; "
            f"assert not pathlib.Path({str(sentinel)!r}).exists(); "
            "assert all(not value.startswith('FAKE-NATIVE-CLIENT-OPERATOR-') for value in os.environ.values()); "
            "pathlib.Path('/work/bash-probe').write_text('sandboxed tool')"
        )
        probe += "\n" + "\n".join(
            [
                "for path in pathlib.Path('/proc').glob('[0-9]*/environ'):",
                "    try: raw = path.read_bytes()",
                "    except OSError: continue",
                "    for field in raw.split(b'\\0'):",
                "        assert not field.startswith(b'ANTHROPIC_API_KEY=FAKE-NATIVE-CLIENT-OPERATOR-')",
            ]
        )
        import shlex

        bash = "/usr/bin/python3 -I -c " + shlex.quote(probe)
        requests = []

        def response(req, path):
            if "count_tokens" in path:
                return "application/json", [b'{"input_tokens":128}', b""]
            used = any(
                isinstance(m.get("content"), list)
                and any(block.get("type") == "tool_result" for block in m["content"])
                for m in req["messages"]
            )
            message = {
                "type": "message_start",
                "message": {
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": req["model"],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 128, "output_tokens": 1},
                },
            }
            block = (
                {"type": "text", "text": ""}
                if used
                else {"type": "tool_use", "id": "tool_fake", "name": "Bash", "input": {}}
            )
            delta = (
                {"type": "text_delta", "text": "Fake API finished."}
                if used
                else {"type": "input_json_delta", "partial_json": json.dumps({"command": bash})}
            )
            events = [
                message,
                {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": "end_turn" if used else "tool_use",
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": 32},
                },
                {"type": "message_stop"},
            ]
            return "text/event-stream", [
                (f"event: {event['type']}\ndata: {json.dumps(event)}\n\n").encode()
                for event in events
            ] + [b""]

        def connection():
            conn = Mock(sock=None)

            def request(method, path, *, body, headers):
                req = json.loads(body)
                requests.append((req, headers))
                content_type, chunks = response(req, path)
                reply = Mock(status=200)
                reply.getheader.side_effect = lambda name, default=None: (
                    content_type if name.lower() == "content-type" else default
                )
                reply.getheaders.return_value = []
                reply.read1.side_effect = chunks
                conn.getresponse.return_value = reply

            conn.request.side_effect = request
            return conn

        command = prepare_client(
            self.controls,
            ChannelSpec("claude_cli", "opus", steps=3, sandbox="mojo-claude", auth_mode="api-key"),
            "Use the provided tool, then finish.",
            "",
        )
        audit_root = self.root / "native-audit"
        audit_root.mkdir()
        attempt = AttemptAudit(
            audit_root,
            label="fake native client",
            cache_key="fixture",
            attempt=1,
            isolation={"fake_api": True},
            mission="Use the provided tool, then finish.",
            system="",
        )
        with ApiBroker(
            self.controls / "api.sock",
            api_key=token,
            model="opus",
            max_calls=4,
            _connection=connection,
        ) as broker:
            result = sandbox.run(
                attempt.work,
                command,
                timeout=30,
                broker=self.controls / "api.sock",
            )
        self.assertEqual(result.returncode, 0, result)
        reply = captured_reply(result)
        self.assertTrue(reply.ok, reply)
        self.assertIn("Fake API finished.", reply.text)
        transcript = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(transcript[-1]["type"], "result")
        blocks = [
            block
            for ev in transcript
            for block in ev.get("message", {}).get("content", [])
            if isinstance(block, dict)
        ]
        self.assertTrue(
            any(b.get("type") == "tool_use" and b.get("name") == "Bash" for b in blocks)
        )
        self.assertTrue(any(b.get("type") == "tool_result" for b in blocks))
        ref = attempt.finish(outcome="accepted", error="", transcript=reply.transcript)
        doc = load_attempt(audit_root, ref)
        self.assertEqual(doc["transcript"], "captured")
        self.assertEqual((attempt.path / "stdout.jsonl").read_text(), result.stdout)
        self.assertEqual((attempt.work / "bash-probe").read_text(), "sandboxed tool")
        self.assertEqual(sentinel.read_text(), "PRIVATE FAKE SENTINEL")
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertGreaterEqual(broker.calls, 2)
        self.assertIn("--bare", command)
        self.assertTrue(all(h["x-api-key"] == token for _, h in requests))
        self.assertTrue(all("Authorization" not in h for _, h in requests))
