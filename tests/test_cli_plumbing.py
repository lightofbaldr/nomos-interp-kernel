"""The CLIs, preflight and sweep plumbing for the pi substrate and the CLI improver/judge."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness2 import config, preflight
from harness2.cli import run_sweep
from harness2.cli._common import SUBSTRATES, make_adapter
from harness2.core import schedule
from harness2.core.proposer import UsageLimitError
from harness2.demo import DemoAdapter, DemoProposer
from harness2.substrate.pi import PiSubstrate

REPO = Path(__file__).resolve().parents[1]


class CommonTests(unittest.TestCase):
    def test_substrates(self):
        self.assertEqual(SUBSTRATES, ("oc", "cx", "pi"))

    def test_wb_on_pi_refused(self):
        with self.assertRaisesRegex(ValueError, "Docker"):
            make_adapter("wb", "office", "pi")


def _fake_adapter():
    return SimpleNamespace(
        spec=SimpleNamespace(name="jb"),
        substrate=PiSubstrate(),
        tasks=lambda split="all": [],
        preflight=Mock(),
    )


class PreflightTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.calls: list[list[str]] = []

        def fake_run(argv, **kw):
            self.calls.append([str(a) for a in argv])
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        self.patches = [
            patch("harness2.preflight.subprocess.run", fake_run),
            patch("harness2.preflight.missing_runners", return_value=""),
            patch.object(config, "DEPENDENCIES", self.td),
            patch.object(config, "SOLVER_MODEL_OC", "zai/glm-5.3-flash"),
            patch.object(config, "SOLVER_MODEL_PI", "zai/glm-5.3-flash"),
            patch.object(config, "IMPROVER_MODEL", "opus"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
            patch.dict(os.environ, {"HARNESS2_SOLVER_MODEL": "zai/glm-5.3-flash", "HARNESS2_JUDGE_MODEL": "cli/codex:x"}),
            patch("harness2.preflight.shutil.which", side_effect=lambda b: f"/bin/{b}"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_preflight_skips_opencode_on_pi(self):
        adapter = _fake_adapter()
        with (
            patch("harness2.preflight.OpencodeSubstrate") as oc,
            patch.object(adapter.substrate, "preflight"),
            patch.object(config, "JUDGE_MODEL", ""),
        ):
            errors = preflight.check(adapter)
        oc.return_value.preflight.assert_not_called()
        adapter.preflight.assert_called_once()
        self.assertFalse(any("opencode" in a for call in self.calls for a in call), self.calls)
        self.assertEqual(errors, [])

    def test_preflight_cli_judge_needs_bridge(self):
        adapter = _fake_adapter()
        with (
            patch("harness2.preflight.OpencodeSubstrate"),
            patch.object(adapter.substrate, "preflight"),
            patch.object(config, "JUDGE_MODEL", "cli/codex:x"),
            patch.object(config, "JB_JUDGE_API_BASE", "http://127.0.0.1:1/v1"),
            patch.object(config, "JB_JUDGE_API_KEY", "k"),
            patch.object(config, "JB_JUDGE_PYTHON", self.td / "python"),
        ):
            errors = preflight.check(adapter, judge=True)
        self.assertTrue(any("harness2-judge-bridge" in e for e in errors), errors)
        self.assertFalse(any("gcloud" in a for call in self.calls for a in call), self.calls)
        self.assertTrue(any(call[:3] == ["/bin/codex", "login", "status"] for call in self.calls), self.calls)


class SweepTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.adapter = DemoAdapter(self.td)

    def test_identity_uses_pi_model(self):
        args = SimpleNamespace(substrate="pi", split="all", seed=None, only_domains="", max_tasks=None)
        with (
            patch.object(config, "SOLVER_MODEL_PI", "zai/glm-5.3-flash"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
        ):
            run_sweep._check_identity(self.adapter, args, self.td / "ns")
        identity = json.loads((self.td / "ns" / "config.json").read_text())
        self.assertEqual(identity["executor"], "zai/glm-5.3-flash")
        self.assertEqual(identity["improver_channel"], "claude_cli")

    def test_identity_accepts_upstream_config_json(self):
        # A namespace written by upstream has no improver_channel key: resuming it must work
        # when the channel is upstream's only one (opencode), and refuse a changed channel.
        args = SimpleNamespace(substrate="oc", split="all", seed=None, only_domains="", max_tasks=None)
        with (
            patch.object(config, "SOLVER_MODEL_OC", "google-vertex/gemini"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "opencode"),
        ):
            run_sweep._check_identity(self.adapter, args, self.td / "legacy")
            path = self.td / "legacy" / "config.json"
            saved = json.loads(path.read_text())
            del saved["improver_channel"]
            path.write_text(json.dumps(saved))
            run_sweep._check_identity(self.adapter, args, self.td / "legacy")  # accepted
        with (
            patch.object(config, "SOLVER_MODEL_OC", "google-vertex/gemini"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
            self.assertRaises(ValueError),
        ):
            run_sweep._check_identity(self.adapter, args, self.td / "legacy")

    def test_run_domain_stops_after_usage_limit(self):
        class Limited(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                self.stopped = True
                raise UsageLimitError("usage limit reached", reset_hint="retry after 3600s")

        prop = Limited(self.adapter.spec.channels["proposer"])
        result = schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            1,
            adapter=self.adapter,
            prop=prop,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.td / "library",
        )
        self.assertTrue(result["stopped"])
        self.assertNotEqual(result["tasks"][0]["status"], "ok")
        self.assertEqual(result["tasks"][1]["status"], "stopped")
        self.assertEqual(result["done"], 0)

    def test_run_sweep_main_returns_3_when_stopped(self):
        rep = {"domains": [{"domain": "d", "done": 0, "failed": 1, "stopped": True}], "stopped": True}
        args = SimpleNamespace(
            k=1, workers=1, modes="parallel", dry_run=False, bench="jb", subset=None, substrate="pi",
            split="all", split_file=None, only_domains="", max_tasks=None, seed=None,
        )
        with (
            patch("harness2.cli.run_sweep.C.make_adapter", return_value=self.adapter),
            patch("harness2.cli.run_sweep._stream_for", return_value=({"a": ["t"]}, [("a", "t")])),
            patch("harness2.preflight.require_ready"),
            patch("harness2.cli.run_sweep.Proposer"),
            patch("harness2.cli.run_sweep.schedule.run_sweep", return_value=rep),
            patch("harness2.cli.run_sweep._check_identity"),
            patch("harness2.cli.run_sweep.C.runs_root_for", return_value=self.td / "runs"),
            patch("harness2.cli.run_sweep.C.library_root_for", return_value=self.td / "lib"),
            patch("builtins.print") as out,
        ):
            rc = run_sweep._main(args)
        self.assertEqual(rc, 3)
        printed = " ".join(str(c.args[0]) for c in out.call_args_list if c.args)
        self.assertIn("usage limit reached; re-run the same command to resume", printed)


class SetupDryRunTests(unittest.TestCase):
    def test_setup_dry_run_pi_skips_opencode(self):
        env = {**os.environ, "HARNESS2_IMPROVER_CHANNEL": "claude_cli"}
        r = subprocess.run(
            [str(REPO / ".venv" / "bin" / "harness2-setup"), "jb", "--substrate", "pi", "--dry-run"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("anomalyco/opencode", r.stdout)  # no OpenCode clone
        self.assertNotIn("bun@", r.stdout)  # no bun install
        self.assertIn("dependencies/pi/agent", r.stdout)

    def test_check_accepts_pi(self):
        r = subprocess.run(
            [str(REPO / ".venv" / "bin" / "harness2-check"), "--bench", "wb", "--subset", "office", "--substrate", "pi"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("Docker", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
