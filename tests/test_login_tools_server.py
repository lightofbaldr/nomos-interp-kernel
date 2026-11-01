import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.substrate.login_tools_server import (
    ALLOWED,
    MAX_MESSAGE,
    OUTPUT_LIMIT,
    TOOLS,
    ProbeSandbox,
    Server,
    encoded,
    serve,
)
from harness2.substrate.sandbox import ProcessResult, SandboxTeardownError


class ToolServer(unittest.TestCase):
    nonce = "a" * 64

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-login-server-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("context", "workspace", "audit"):
            (self.root / name).mkdir()
        (self.root / "audit/admission.json").write_bytes(
            encoded({"tools": list(ALLOWED), "nonce": self.nonce})
        )
        (self.root / "context/source").write_text("context")
        self.sandbox = Mock(identity=Mock(return_value={"fixture": 1}))
        self.server = Server(
            self.root / "context",
            self.root / "workspace",
            self.root / "audit",
            self.sandbox,
            nonce=self.nonce,
        )
        self.addCleanup(self.server.close)

    def call(self, name, **args):
        return self.server.call(name, args)

    def test_mcp_handshake_inventory_read_write_and_no_extra_capabilities(self):
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {"path": "context/source"}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "workspace/out", "content": "candidate"},
                },
            },
        ]
        out = io.BytesIO()
        serve(self.server, io.BytesIO(b"".join(map(encoded, requests))), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0]["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], list(TOOLS))
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertEqual(replies[3]["error"]["code"], -32601)
        self.assertEqual((self.root / "workspace/out").read_text(), "candidate")
        records = [
            json.loads(line) for line in (self.root / "audit/calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [v["event"] for v in records], ["open", "call", "result", "call", "result", "close"]
        )
        self.assertEqual(records[1]["path"], "context/source")
        self.assertEqual(records[3]["path"], "workspace/out")
        self.assertNotIn("candidate", (self.root / "audit/calls.jsonl").read_text())
        self.assertFalse(self.sandbox.run.called)

    def test_unknown_tools_and_argument_injection_refused_and_logged(self):
        for name, args in (
            ("Bash", {"path": "workspace"}),
            ("read_file", {"path": "context/source", "shell": "anything"}),
            ("write_file", {"path": "../outside", "content": "bad"}),
            ("read_file", {"path": "https://example.org/"}),
            ("read_file", {"path": "/proc/self/environ"}),
        ):
            with self.subTest(name=name, args=args):
                self.assertTrue(self.server.call(name, args)["isError"])
        self.assertFalse(self.sandbox.run.called)
        records = [
            json.loads(line) for line in (self.root / "audit/calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(sum(v["event"] == "result" for v in records), 5)

    def test_no_tool_side_effect_before_parent_admits_inventory(self):
        (self.root / "audit/admission.json").unlink()
        for tool, arguments in (
            ("list_dir", {"path": "context"}),
            ("read_file", {"path": "context/source"}),
            ("write_file", {"path": "workspace/a", "content": "no"}),
            ("run_mojo", {"path": "workspace/a.mojo"}),
        ):
            with self.subTest(tool=tool):
                result = self.server.call(tool, arguments)
                self.assertTrue(result["isError"])
                self.assertIn("not admitted", result["content"][0]["text"])
        self.assertFalse((self.root / "workspace/a").exists())
        self.assertFalse(self.sandbox.run.called)
        (self.root / "audit/admission.json").write_bytes(
            encoded({"tools": list(ALLOWED), "nonce": self.nonce})
        )
        self.assertFalse(self.call("read_file", path="context/source")["isError"])

    def test_stale_nonce_and_reachable_admission_are_refused(self):
        admission = self.root / "audit/admission.json"
        for nonce in ("b" * 64, None):
            admission.write_bytes(encoded({"tools": list(ALLOWED), "nonce": nonce}))
            self.assertTrue(self.call("read_file", path="context/source")["isError"])
        admission.write_bytes(encoded({"tools": list(ALLOWED), "nonce": self.nonce}))
        before = admission.read_bytes()
        for path in (
            str(admission),
            "../audit/admission.json",
            "workspace/../audit/admission.json",
            "context/../audit/admission.json",
        ):
            self.assertTrue(self.call("read_file", path=path)["isError"])
            self.assertTrue(self.call("write_file", path=path, content="forged")["isError"])
        self.assertEqual(admission.read_bytes(), before)
        self.assertFalse(self.call("read_file", path="context/source")["isError"])

    def test_call_budget_and_malformed_protocol(self):
        with patch("harness2.substrate.login_tools_server.MAX_CALLS", 1):
            self.assertFalse(self.call("list_dir", path="workspace")["isError"])
            self.assertTrue(self.call("list_dir", path="workspace")["isError"])
        for msg in (
            [],
            {"jsonrpc": "1.0"},
            {"jsonrpc": "2.0", "id": True},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ):
            with self.subTest(msg=msg), self.assertRaises(ValueError):
                self.server.dispatch(msg)

    def test_oversized_incomplete_duplicate_and_nonfinite_json_refused(self):
        for raw in (b"x" * (MAX_MESSAGE + 1), b"{}", b'{"id":1,"id":2}\n', b'{"id":NaN}\n'):
            with self.subTest(raw=raw[:32]):
                other = self.root / ("audit-" + str(len(list(self.root.iterdir()))))
                other.mkdir()
                server = Server(
                    self.root / "context",
                    self.root / "workspace",
                    other,
                    self.sandbox,
                    nonce=self.nonce,
                )
                with self.assertRaises(ValueError):
                    serve(server, io.BytesIO(raw), io.BytesIO())

    def test_probe_uses_private_snapshot_fixed_argv_and_limits(self):
        code = 'def main():\n    print("probe-ok")\n'
        self.call("write_file", path="workspace/probe.mojo", content=code)
        invocations = []

        def execute(work, cmd, **kwargs):
            invocations.append((work, cmd, kwargs))
            if len(invocations) == 1:
                self.assertEqual((work / "solution.mojo").read_text(), code)
                (work / "program").write_bytes(b"fake executable, never run")
                (work / "program").chmod(0o700)
                # A model program cannot mutate its caller's actual workspace.
                (work / "solution.mojo").write_text("changed private copy")
            return ProcessResult(0, "probe-ok\n", "", 0.1)

        self.sandbox.run.side_effect = execute
        result = self.call("run_mojo", path="workspace/probe.mojo")
        self.assertFalse(result["isError"])
        doc = json.loads(result["content"][0]["text"])
        self.assertEqual(doc["run"]["stdout"], "probe-ok\n")
        self.assertEqual(
            invocations[0][1],
            ["/opt/mojo/bin/mojo", "build", "/work/solution.mojo", "-o", "/work/program"],
        )
        self.assertEqual(invocations[1][1], ["/work/program"])
        self.assertEqual(
            [v[2] for v in invocations],
            [
                {"timeout": 90, "output_limit": OUTPUT_LIMIT},
                {"timeout": 10, "output_limit": OUTPUT_LIMIT},
            ],
        )
        self.assertNotEqual(invocations[0][0], self.root / "workspace")
        self.assertEqual((self.root / "workspace/probe.mojo").read_text(), code)

    def test_compile_timeout_failure_and_output_limit_never_run_program(self):
        self.call("write_file", path="workspace/probe.mojo", content="bad source")
        for reason, code in (("timeout", 124), ("output_limit", 137), (None, 1)):
            self.sandbox.run.reset_mock()
            self.sandbox.run.return_value = ProcessResult(code, "", "refused", 0.1, reason)
            out = self.call("run_mojo", path="workspace/probe.mojo")
            doc = json.loads(out["content"][0]["text"])
            self.assertEqual(doc["compile"]["reason"], reason)
            self.assertIsNone(doc["run"])
            self.assertEqual(self.sandbox.run.call_count, 1)

    def test_shared_runner_final_chunk_is_trimmed_and_never_a_success(self):
        self.call("write_file", path="workspace/probe.mojo", content="source")
        self.sandbox.run.return_value = ProcessResult(0, "x" * (OUTPUT_LIMIT + 10), "stderr", 0.1)
        doc = json.loads(self.call("run_mojo", path="workspace/probe.mojo")["content"][0]["text"])
        self.assertEqual(
            len(doc["compile"]["stdout"].encode()) + len(doc["compile"]["stderr"].encode()),
            OUTPUT_LIMIT,
        )
        self.assertEqual(doc["compile"]["reason"], "output_limit")
        self.assertIsNone(doc["run"])

    def test_no_arbitrary_probe_path_flags_network_or_home(self):
        for path in (
            "context/probe.mojo",
            "/tmp/probe.mojo",
            "workspace/a.py",
            "workspace/../probe.mojo",
        ):
            self.assertTrue(self.call("run_mojo", path=path)["isError"])
        args = ProbeSandbox().argv(self.root / "workspace", ["/usr/bin/true"])
        self.assertIn("--unshare-all", args)
        self.assertIn("--clearenv", args)
        self.assertNotIn("--share-net", args)
        self.assertNotIn("/model.sock", args)
        self.assertNotIn(str(Path.home()), args)
        self.assertFalse(self.sandbox.run.called)

    def test_probe_budget_and_teardown_failure_stop(self):
        self.call("write_file", path="workspace/probe.mojo", content="source")
        with patch("harness2.substrate.login_tools_server.MAX_PROBES", 0):
            self.assertTrue(self.call("run_mojo", path="workspace/probe.mojo")["isError"])
        self.sandbox.run.side_effect = SandboxTeardownError("unsettled")
        with self.assertRaises(SandboxTeardownError):
            self.call("run_mojo", path="workspace/probe.mojo")

    def test_audit_failure_prevents_side_effect(self):
        with patch.object(self.server, "append", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.call("write_file", path="workspace/must-not-exist", content="bad")
        self.assertFalse((self.root / "workspace/must-not-exist").exists())

    def test_same_audit_cannot_be_adopted_or_server_restarted(self):
        with self.assertRaises(FileExistsError):
            Server(
                self.root / "context",
                self.root / "workspace",
                self.root / "audit",
                self.sandbox,
                nonce=self.nonce,
            )

    def test_confirmed_probe_teardown_required(self):
        with patch("harness2.substrate.sandbox.subprocess.run") as run:
            run.return_value = Mock(
                returncode=0, stdout="LoadState=not-found\nActiveState=inactive\nControlGroup=\n"
            )
            ProbeSandbox()._stop_unit("fixture")
            run.return_value.stdout = (
                "LoadState=loaded\nActiveState=active\nControlGroup=/still-alive\n"
            )
            with self.assertRaises(SandboxTeardownError):
                ProbeSandbox()._stop_unit("fixture")

    def test_tool_allowlist_matches_schema(self):
        self.assertEqual(ALLOWED, tuple("mcp__harness2__" + name for name in TOOLS))
