"""Improver channels: claude_cli on the login, codex_cli, and the plan-limit wait."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config
from harness2.core.proposer import Proposer, ProposerError, UsageLimitError
from harness2.core.spec import ChannelSpec

CLAUDE_OK = '{"result":"<notes>ok</notes>","total_cost_usd":0.5,"subtype":"success"}'
CLAUDE_LIMITED = (
    '{"is_error":true,"result":"You have reached your usage limit. Resets at 4pm",'
    '"subtype":"error"}'
)


class FakeRun:
    """Records every subprocess.run call; answers from a queue of (stdout, rc, stderr) or callables."""

    def __init__(self, *responses):
        self.calls: list[tuple[list[str], dict]] = []
        self.responses = list(responses)

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(r):
            return r(list(argv), kw)
        stdout, rc, stderr = r
        return SimpleNamespace(stdout=stdout, returncode=rc, stderr=stderr)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.sleeps: list[float] = []
        sleep = patch("harness2.core.proposer.time.sleep", side_effect=self.sleeps.append)
        sleep.start()
        self.addCleanup(sleep.stop)

    def _call(self, spec, fake, **kw):
        with patch("harness2.core.proposer.subprocess.run", fake):
            prop = Proposer(spec)
            return prop, prop.call(kw.pop("mission", "mission"), None, "lbl", **kw)

    def test_claude_argv_and_env(self):
        fake = FakeRun((CLAUDE_OK, 0, ""))
        with patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x"}):
            _prop, reply = self._call(ChannelSpec("claude_cli", "opus", effort="high"), fake)
        argv, kw = fake.calls[0]
        self.assertEqual(reply.text, "<notes>ok</notes>")
        self.assertEqual(reply.cost_usd, 0.5)
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertIn("--setting-sources", argv)
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--bare", argv)
        self.assertNotIn("CLAUDECODE", kw["env"])
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", kw["env"])
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", kw["env"])
        self.assertEqual(kw["input"], "mission")

    def test_claude_vertex_opt_in(self):
        fake = FakeRun((CLAUDE_OK, 0, ""))
        with (
            patch.object(config, "CLAUDE_USE_VERTEX", True),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}),
        ):
            self._call(ChannelSpec("claude_cli", "opus"), fake)
        _argv, kw = fake.calls[0]
        self.assertEqual(kw["env"]["CLAUDE_CODE_USE_VERTEX"], "1")
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])

    def test_login_drops_inherited_api_key_but_bare_keeps_it(self):
        for isolation in ("flags", "bare"):
            with self.subTest(isolation=isolation):
                fake = FakeRun((CLAUDE_OK, 0, ""))
                with (
                    patch.object(config, "CLAUDE_USE_VERTEX", False),
                    patch.object(config, "CLAUDE_ISOLATION", isolation),
                    patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-parent-key", "CLAUDECODE": "1"}, clear=True),
                ):
                    self._call(ChannelSpec("claude_cli", "opus"), fake)
                    self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "fake-parent-key")
                argv, kw = fake.calls[0]
                self.assertEqual("--bare" in argv, isolation == "bare")
                self.assertNotIn("CLAUDECODE", kw["env"])
                if isolation == "bare":
                    self.assertEqual(kw["env"].get("ANTHROPIC_API_KEY"), "fake-parent-key")
                else:
                    self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])

    def test_codex_channel_reads_last_message(self):
        def run(argv, kw):
            Path(argv[argv.index("-o") + 1]).write_text("<notes>done</notes>", encoding="utf-8")
            return SimpleNamespace(
                stdout='{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":1}}\n',
                returncode=0,
                stderr="",
            )

        fake = FakeRun(run)
        _prop, reply = self._call(
            ChannelSpec("codex_cli", "gpt-5.6-sol", effort="high"), fake, system="S"
        )
        argv, kw = fake.calls[0]
        self.assertEqual(reply.text, "<notes>done</notes>")
        self.assertEqual(argv[1], "exec")
        self.assertEqual(argv[-1], "-")
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.6-sol")
        self.assertTrue(kw["input"].startswith("# SYSTEM INSTRUCTIONS"))
        self.assertTrue(kw["input"].endswith("mission"))
        self.assertEqual(Path(argv[argv.index("-o") + 1]).name, ".harness2_last_message.txt")
        self.assertEqual(reply.usage.get("input_tokens"), 5)

    def test_usage_limit_waits_then_succeeds(self):
        fake = FakeRun((CLAUDE_LIMITED, 1, ""), (CLAUDE_LIMITED, 1, ""), (CLAUDE_OK, 0, ""))
        with patch.object(config, "LIMIT_MAX_WAIT", 1000), patch.object(config, "STOP_ON_LIMIT", False):
            _prop, reply = self._call(ChannelSpec("claude_cli", "opus"), fake)
        self.assertEqual(self.sleeps, [300, 600])
        self.assertTrue(reply.ok)
        self.assertEqual(reply.attempts, 1)
        self.assertEqual(len(fake.calls), 3)

    def test_usage_limit_budget_exhausted_falls_back_to_proposer_error(self):
        fake = FakeRun((CLAUDE_LIMITED, 1, ""))
        with (
            patch.object(config, "LIMIT_MAX_WAIT", 0),
            patch.object(config, "STOP_ON_LIMIT", False),
            self.assertRaisesRegex(ProposerError, "usage limit"),
        ):
            self._call(ChannelSpec("claude_cli", "opus"), fake)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn(300, self.sleeps)

    def test_stop_on_limit_raises_usage_limit_error(self):
        fake = FakeRun((CLAUDE_LIMITED, 1, ""))
        with (
            patch.object(config, "STOP_ON_LIMIT", True),
            patch("harness2.core.proposer.subprocess.run", fake),
        ):
            prop = Proposer(ChannelSpec("claude_cli", "opus"))
            with self.assertRaises(UsageLimitError):
                prop.call("mission", None, "lbl")
        self.assertTrue(prop.stopped)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(fake.calls), 1)

    def test_opencode_channel_still_returns_text(self):
        fake = FakeRun(('{"type":"text","part":{"text":"done"}}', 0, ""))
        with tempfile.TemporaryDirectory() as td:
            with patch("harness2.core.proposer._opencode_dir", return_value=Path(td)):
                _prop, reply = self._call(ChannelSpec("opencode", "local/x"), fake)
        self.assertEqual(reply.text, "done")

    def test_audit_meta_records_usage(self):
        fake = FakeRun(('{"result":"r","subtype":"success","usage":{"input_tokens":7}}', 0, ""))
        with tempfile.TemporaryDirectory() as td, patch(
            "harness2.core.proposer.subprocess.run", fake
        ):
            prop = Proposer(ChannelSpec("claude_cli", "opus"), audit_dir=Path(td))
            prop.call("mission", None, "lbl")
            meta = json.loads(next(Path(td).glob("lbl.*.meta.json")).read_text())
        self.assertEqual(meta["usage"], {"input_tokens": 7})


if __name__ == "__main__":
    unittest.main()
