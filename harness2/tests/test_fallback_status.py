"""Offline witnesses for exhausted improvement stages, not legitimate no-op edits."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2.cli import run_sweep
from harness2.core import recursion, schedule
from harness2.core.proposer import ProposerError, Reply
from harness2.demo import DemoAdapter, DemoProposer


class FailingImprover(DemoProposer):
    def call(self, mission, context_dir, label, **kwargs):
        raise ProposerError("Invalid API key · Fix external API key", attempts=1)


class NoOpImprover(DemoProposer):
    def call(self, mission, context_dir, label, **kwargs):
        if label.startswith("experience."):
            return Reply(label, "NO_ENTRY", True)
        return Reply(
            label,
            "<notes>Retain the input harness deliberately.</notes>",
            True,
            workspace=Path(context_dir) / "harness",
            attempts=1,
        )


class FallbackStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-fallback-status-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.adapter = DemoAdapter(self.root)
        self.failed = FailingImprover(self.adapter.spec.channels["proposer"])

    def task(self, prop=None, modes=("agg",)):
        return schedule.run_task(
            "ledger-1",
            1,
            adapter=self.adapter,
            prop=prop or self.failed,
            runs_root=self.adapter.spec.runs_root,
            modes=modes,
        )

    @property
    def summary(self):
        return self.adapter.spec.runs_root / "demo-oc-agg1/ledger-1/summary.json"

    def test_all_stages_fallback_is_not_success_in_either_mode(self):
        result = self.task(modes=("agg", "seq"))
        self.assertEqual(result.status, "fallback-only")
        self.assertEqual(json.loads(self.summary.read_text())["status"], "fallback-only")
        for mode in ("agg", "seq"):
            self.assertTrue(result.modes[mode]["fallback_only"])
            self.assertFalse(result.modes[mode]["ok"])
            self.assertEqual(len(result.modes[mode]["fell_back"]), 3)
            self.assertTrue(result.selections[mode]["chosen_tree_is_base"])

    def test_accepted_noop_is_not_a_failure_fallback(self):
        prop = NoOpImprover(self.failed.spec)
        result = self.task(prop=prop)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.modes["agg"]["ok"])
        self.assertFalse(result.modes["agg"]["fallback_only"])
        self.assertEqual(result.modes["agg"]["fell_back"], [])
        self.assertEqual(result.modes["agg"]["kept_base"], ["inv1", "dom1", "agg1"])

    def test_some_stage_fallback_does_not_mask_a_successful_composition(self):
        class Composer(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label.startswith(("inv", "dom")):
                    raise ProposerError("proposal failed")
                return super().call(mission, context_dir, label, **kwargs)

        result = self.task(prop=Composer(self.failed.spec))
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.modes["agg"]["fallback_only"])
        self.assertFalse(result.selections["agg"]["chosen_tree_is_base"])

    def test_one_fallback_mode_makes_multimode_task_partial(self):
        original = recursion.run_mode
        good = DemoProposer(self.failed.spec)

        def by_mode(mode, *args, **kw):
            return original(mode, *args, **(kw | {"prop": self.failed if mode == "agg" else good}))

        with patch.object(recursion, "run_mode", side_effect=by_mode):
            result = self.task(modes=("agg", "seq"))
        self.assertEqual(result.status, "partial")
        saved = json.loads(self.summary.read_text())
        self.assertEqual(saved["status"], "partial")
        self.assertTrue(saved["modes"]["agg"]["fallback_only"])
        self.assertFalse(saved["modes"]["seq"]["fallback_only"])
        self.assertTrue(any("fallback-only" in error for error in result.errors))

    def test_resume_of_fallback_and_old_ok_record_never_becomes_success_or_retries(self):
        self.task()
        original = json.loads(self.summary.read_text())
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                record = json.loads(json.dumps(original))
                if legacy:
                    record["status"] = "ok"
                    record["errors"] = []
                    record["modes"]["agg"]["ok"] = True
                    record["modes"]["agg"].pop("fallback_only", None)
                self.summary.write_text(json.dumps(record))
                before = self.summary.read_bytes()
                with (
                    patch.object(
                        self.adapter, "execute", side_effect=AssertionError("no re-execution")
                    ),
                    patch.object(self.failed, "call", side_effect=AssertionError("no new session")),
                ):
                    result = self.task()
                self.assertEqual(result.status, "fallback-only")
                self.assertTrue(result.modes["agg"]["fallback_only"])
                self.assertFalse(result.modes["agg"]["ok"])
                self.assertEqual(self.summary.read_bytes(), before)

    def test_sweep_line_totals_and_real_cli_status_are_nonzero(self):
        with (
            patch("harness2.cli.run_sweep.C.make_adapter", return_value=self.adapter),
            patch("harness2.preflight.require_ready"),
            patch("harness2.cli.run_sweep.Proposer", return_value=self.failed),
            patch("harness2.cli.run_sweep._check_identity"),
            patch(
                "harness2.core.selection.reflect_adoption",
                side_effect=AssertionError("fallback-only must not trigger reflection"),
            ),
            redirect_stdout(io.StringIO()) as out,
        ):
            rc = run_sweep.main(
                [
                    "--bench",
                    "jb",
                    "--substrate",
                    "oc",
                    "--split",
                    "train",
                    "--k",
                    "1",
                    "--modes",
                    "parallel",
                    "--max-tasks",
                    "1",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("fallback-only", out.getvalue())
        self.assertIn("0 tasks ok", out.getvalue())
        record = json.loads(
            next((self.adapter.spec.runs_root / "adapt/_sweeps").glob("*.json")).read_text()
        )
        domain = record["domains"][0]
        self.assertEqual((domain["done"], domain["failed"], domain["fallback_only"]), (0, 1, 1))
        self.assertEqual(domain["tasks"][0]["status"], "fallback-only")
        self.assertFalse(list((self.adapter.spec.runs_root / "library").rglob("EXPERIENCE.md")))
