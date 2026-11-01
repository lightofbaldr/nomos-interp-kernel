import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.substrate import login_tools_control as control
from harness2.substrate.login_tools_server import ALLOWED, Server, digest
from harness2.substrate.sandbox import ProcessResult


class ControlOracle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-control-oracle-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def fixture(self, *, omit=False, broken_probe=False):
        attempt = self.root / "attempt"
        for name in ("work/context", "work/workspace", "login-tools"):
            (attempt / name).mkdir(parents=True)
        (attempt / "login-tools/admission.json").write_text(
            json.dumps({"tools": list(ALLOWED), "nonce": "a" * 64})
        )
        (attempt / "work/context/allowed.txt").write_text(control.TEXT)
        (attempt / "stdout.jsonl").write_text(
            json.dumps({"type": "system", "subtype": "init", "tools": list(ALLOWED)}) + "\n"
        )
        outside = self.root / "outside"
        outside.write_text("sentinel")
        sha = digest(outside.read_bytes())
        paths = control.forbidden_paths(Path("/fake/home"), outside)
        sandbox = Mock(identity=Mock(return_value={"fake": 1}))

        def execute(work, cmd, **kwargs):
            (work / "program").write_text("fixture only")
            (work / "program").chmod(0o700)
            return ProcessResult(1 if broken_probe else 0, "harness2-login-tools-ok\n", "", 0.1)

        sandbox.run.side_effect = execute
        server = Server(
            attempt / "work/context",
            attempt / "work/workspace",
            attempt / "login-tools",
            sandbox,
            nonce="a" * 64,
        )
        for path in paths[:-1]:
            if not omit:
                server.call("read_file", {"path": path})
        server.call("write_file", {"path": str(outside), "content": "bad"})
        server.call("read_file", {"path": "context/allowed.txt"})
        server.call("write_file", {"path": "workspace/allowed.txt", "content": control.TEXT})
        server.call("write_file", {"path": "workspace/control.mojo", "content": control.PROBE})
        server.call("run_mojo", {"path": "workspace/control.mojo"})
        server.close()
        return attempt, {"bad_paths": paths, "outside": outside, "outside_sha": sha}

    def test_positive_oracle_uses_actual_tool_evidence(self):
        path, kwargs = self.fixture()
        self.assertTrue(control.inspect_control(path, **kwargs)["pass"])

    def test_claims_without_actual_refusal_challenges_do_not_pass(self):
        path, kwargs = self.fixture(omit=True)
        self.assertFalse(control.inspect_control(path, **kwargs)["pass"])

    def test_bad_compile_or_changed_outside_fails(self):
        path, kwargs = self.fixture(broken_probe=True)
        self.assertFalse(control.inspect_control(path, **kwargs)["pass"])
        kwargs["outside"].write_text("overwritten")
        self.assertIn(
            "outside sentinel changed", control.inspect_control(path, **kwargs)["problems"]
        )

    def test_no_opt_in_means_no_directory_or_client(self):
        with (
            patch.dict(
                os.environ,
                {"HARNESS2_CC_LOGIN_CONTROL_APPROVED": "", "HARNESS2_MOJO_LIVE_APPROVED": "1"},
            ),
            patch.object(control.Proposer, "call", side_effect=AssertionError("live client")),
            self.assertRaises(ValueError),
        ):
            control.run(self.root / "must-not-exist", "opus")
        self.assertFalse((self.root / "must-not-exist").exists())
