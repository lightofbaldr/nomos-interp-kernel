"""The actual recursive Harness² method, with fake Pi processes and a scripted improver."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_pi_runner import Runner, load_completed
from harness2.benchmarks.mojo_tasks import Corpus
from harness2.cli import mojo as cli
from harness2.core import schedule
from harness2.core.proposer import Proposer, Reply
from harness2.core.spec import ChannelSpec
from harness2.substrate.sandbox import ProcessResult


class FakePiSandbox:
    def __init__(self, stop="stop"):
        self.calls = 0
        self.stop = stop

    def identity(self):
        return {"offline": True}

    def run(self, work, args, **kw):
        self.calls += 1
        (work / "solution.mojo").write_text("def main():\n    print(7)\n")
        events = [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": self.stop,
                    "content": [{"type": "text", "text": "Saved the program."}],
                },
            },
            {"type": "agent_settled", "aborted": False},
        ]
        return ProcessResult(0, "\n".join(json.dumps(e) for e in events), "", 0)


class OfflineImprover(Proposer):
    def call(self, mission, context_dir, label, **kwargs):
        root = Path(context_dir)
        for p in root.rglob("*.md"):
            text = p.read_text()
            assert "SECRET_REFERENCE" not in text and "SECRET_EXPECTATION" not in text
        if label.startswith("experience."):
            result = Reply(label=label, text="NO_ENTRY", ok=True, attempts=1)
        elif label.startswith("select."):
            name = sorted((root / "candidates").glob("*.md"))[0].stem
            result = Reply(
                label=label,
                text=f"<why>Program delivered.</why>\n```choice\n{name}\n```",
                ok=True,
                attempts=1,
            )
        else:
            harness = root / "harness"
            (harness / "tool_descriptions/tool_guidance.md").write_text(
                "Check compiler diagnostics before finishing.\n"
            )
            result = Reply(
                label=label,
                text="<notes>Prefer compiler feedback.</notes>",
                workspace=harness,
                ok=True,
                attempts=1,
            )
        self._calls.append(result)
        return result


class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h2-method-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        train, test = self.root / "train", self.root / "test"
        train.write_text(
            json.dumps(
                {
                    "id": "train",
                    "prompt": "write a function",
                    "kind": "run",
                    "expect_stdout": "SECRET_EXPECTATION",
                }
            )
        )
        test.write_text(
            json.dumps(
                {
                    "id": "held",
                    "prompt": "unseen function",
                    "test": 'def main():\n    print("ok")',
                    "reference": "SECRET_REFERENCE",
                }
            )
        )
        self.sandbox = FakePiSandbox()
        self.adapter = MojoAdapter(
            corpus=Corpus(train, test),
            runs_root=self.root / "runs",
            runner=Runner(self.sandbox, approved=True),
            sandbox=self.sandbox,
        )

    def test_k1_uses_pi_adapter_and_keeps_grade_out_of_improvement(self):
        proposer = OfflineImprover(ChannelSpec("claude_cli", "offline"))
        with patch.object(
            self.adapter, "score", side_effect=AssertionError("grade during adaptation")
        ):
            result = schedule.run_task(
                "train",
                1,
                adapter=self.adapter,
                prop=proposer,
                runs_root=self.root / "adapt",
                modes=("agg",),
                selector_prop=proposer,
            )
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(self.sandbox.calls, 4)
        self.assertEqual(result.modes["agg"]["ledger"]["R"], 4)

    def test_model_error_even_after_settled_is_unscored(self):
        self.sandbox.stop = "error"
        with self.assertRaisesRegex(RuntimeError, "not settle"):
            self.adapter.execute("train", None, "failed")
        run = self.root / "runs/executions/failed"
        self.assertTrue((run / "failed.json").exists())
        self.assertFalse((run / "complete.json").exists())

    def test_partial_and_changed_completed_run_refuse(self):
        run = self.adapter.execute("train", None, "first")
        self.assertTrue(self.adapter.artifacts_intact(run))
        saved = json.loads((run / "complete.json").read_text())
        saved["outputs"].pop("stderr.txt")
        (run / "complete.json").write_text(json.dumps(saved))
        self.assertFalse(self.adapter.artifacts_intact(run))
        with self.assertRaises(ValueError):
            load_completed(run)
        with self.assertRaises(ValueError):
            self.adapter.execute("train", None, "first")
        self.assertEqual(self.sandbox.calls, 1)

    def test_live_gate_before_any_rollout_files(self):
        adapter = MojoAdapter(
            corpus=self.adapter.corpus, runs_root=self.root / "new", sandbox=self.sandbox
        )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            adapter.execute("train", None, "never")
        self.assertFalse((self.root / "new/executions/never").exists())

    def test_run_names_keep_nested_namespaces_but_refuse_traversal(self):
        for name in ("../outside", "/absolute", "a//b", "a/./b", "a/../b", "a/", "a/\\b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.adapter.execute("train", None, name)
        run = self.adapter.execute("train", None, "mojo-pi-agg1/train/base-0")
        self.assertEqual(run, self.root / "runs/executions/mojo-pi-agg1/train/base-0")
        self.assertEqual(self.sandbox.calls, 1)

    def test_frozen_heldout_cli_does_not_adapt_or_require_improver(self):
        harness = self.adapter.base_harness()
        with (
            patch.object(cli, "MojoAdapter", return_value=self.adapter),
            patch.object(self.adapter, "preflight") as preflight,
            patch.object(self.adapter, "score", return_value={"all_pass": False}),
            patch.dict("os.environ", {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
        ):
            self.assertEqual(cli.main(["evaluate", "--harness", str(harness)]), 0)
        preflight.assert_called_once_with(improver=False)
        self.assertEqual(self.sandbox.calls, 1)
        self.assertEqual(
            len(list((self.root / "runs/executions").glob("evaluate-test-*/held/complete.json"))), 1
        )

    def test_heldout_dry_run_never_starts_runtime(self):
        harness = self.adapter.base_harness()
        with (
            patch.object(cli, "MojoAdapter", return_value=self.adapter),
            patch.object(self.adapter, "preflight", side_effect=AssertionError("preflight")),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(["evaluate", "--harness", str(harness), "--dry-run"]), 0)
        self.assertEqual(json.loads(out.getvalue())["tasks"], ["held"])
        self.assertEqual(self.sandbox.calls, 0)

    def test_unscored_grade_remains_unknown_in_side_by_side_report(self):
        run = self.adapter.execute("train", None, "nested/train/base")
        task = self.adapter.corpus.tasks["train"]
        truth = {
            "status": "unscored",
            "passed": None,
            "oracle": "compile+stdout",
            "reason": "toolchain_health",
            "task_sha256": task.sha256,
            "code_sha256": hashlib.sha256((run / "solution.mojo").read_bytes()).hexdigest(),
        }
        with (
            patch("harness2.benchmarks.mojo.grade", return_value=truth),
            patch(
                "harness2.benchmarks.mojo.MuseJudge",
                side_effect=AssertionError("judge must not run"),
            ),
        ):
            self.assertIsNone(self.adapter.score(run, "train"))
        with (
            patch.object(cli, "MojoAdapter", return_value=self.adapter),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(["report"]), 0)
        row = json.loads(out.getvalue())["rows"][0]
        self.assertEqual(row["run"], "nested/train/base")
        self.assertEqual(row["status"], "unscored")
        self.assertIsNone(row["ground_truth"])
