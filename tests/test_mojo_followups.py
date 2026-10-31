"""Offline regressions shaped by the recorded 33-turn / 32-call cap failure.

No historical task bodies, answers, run artifacts, live services or credentials.
"""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_mojo_workflow import FakePiSandbox, OfflineImprover

from harness2.benchmarks import mojo_pi_runner as runner
from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_tasks import Corpus
from harness2.cli import mojo as cli
from harness2.core import prompts, schedule
from harness2.core.library import Library
from harness2.core.spec import BenchPrompts, ChannelSpec
from harness2.substrate.model_broker import error_detail
from harness2.substrate.sandbox import ProcessResult

FAIL_TASK = "generics-longest-nondecreasing-run"


class OfflineBroker:
    def __init__(self, *args, **kwargs):
        self.calls = 32
        self.last_failure = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class CapSandbox(FakePiSandbox):
    def __init__(self, fail_names=()):
        super().__init__()
        self.fail_names = set(fail_names)

    def run(self, work, args, **kw):
        if work.parent.name not in self.fail_names:
            return super().run(work, args, **kw)
        self.calls += 1
        events = [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "toolUse",
                    "content": [{"type": "text", "text": "COMPILE_ON_WRITE_ATTEMPT"}],
                },
            },
            *({"type": "turn_end"} for _ in range(33)),
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "429 status code (no body)",
                    "content": [],
                },
            },
            {"type": "agent_settled", "aborted": False},
        ]
        return ProcessResult(0, "\n".join(map(json.dumps, events)), "", 0)


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-followups-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        train, held = self.root / "train", self.root / "held"
        train.write_text(
            json.dumps(
                {
                    "id": "train",
                    "prompt": "Training fixture",
                    "kind": "run",
                    "expect_stdout": "SECRET_EXPECTATION",
                }
            )
        )
        held.write_text(
            "\n".join(
                json.dumps(
                    {
                        "id": name,
                        "prompt": name + " fixture",
                        "kind": "run",
                        "expect_stdout": "SECRET_EXPECTATION",
                    }
                )
                for name in [FAIL_TASK, "zz-next"]
            )
        )
        self.sandbox = CapSandbox([FAIL_TASK])
        self.adapter = MojoAdapter(
            corpus=Corpus(train, held),
            runs_root=self.root / "runs",
            sandbox=self.sandbox,
            runner=runner.Runner(self.sandbox, approved=True, _broker=OfflineBroker),
        )
        self.harness = self.adapter.base_harness()
        self.digest = hashlib.sha256(
            json.dumps(runner.harness_tree(self.harness), sort_keys=True).encode()
        ).hexdigest()
        self.run_id = f"evaluate-test-{self.digest[:12]}/{FAIL_TASK}"
        self.failed = self.root / "runs/executions" / self.run_id

    def call_cli(self, argv):
        def grade(task, code, sandbox):
            return {
                "status": "graded",
                "passed": True,
                "oracle": "fixture",
                "task_sha256": task.sha256,
                "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            }

        with (
            patch.object(cli, "MojoAdapter", return_value=self.adapter),
            patch.object(self.adapter, "preflight"),
            patch("harness2.benchmarks.mojo.grade", side_effect=grade),
            patch("harness2.benchmarks.mojo.MuseJudge") as judge,
            patch.dict("os.environ", {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
            redirect_stdout(io.StringIO()) as output,
        ):
            judge.return_value.review.return_value = {"status": "not_requested", "scores": None}
            code = cli.main(argv)
            return code, output.getvalue(), judge.call_count

    def evaluate(self):
        return self.call_cli(["evaluate", "--harness", str(self.harness)])

    def rows(self):
        return json.loads(
            (self.root / "runs" / f"evaluate-test-{self.digest[:12]}.json").read_text()
        )["rows"]

    def create_failure(self):
        with self.assertRaises(RuntimeError):
            self.adapter.execute(FAIL_TASK, self.harness, self.run_id)


class Evaluate(Fixture):
    def test_cap_failure_is_fail_no_muse_and_next_task_still_runs(self):
        code, output, judge_calls = self.evaluate()
        self.assertEqual(code, 0)
        first, second = self.rows()
        self.assertEqual(first["task"], FAIL_TASK)
        self.assertEqual(first["score"]["ground_truth"]["status"], "rollout_failed")
        self.assertIs(first["score"]["ground_truth"]["passed"], False)
        self.assertIsNone(first["score"]["muse_quality"])
        self.assertEqual(first["model_calls"], 32)
        self.assertIn("call cap reached at 32", first["reason"])
        self.assertIs(second["score"]["all_pass"], True)
        self.assertEqual((self.sandbox.calls, judge_calls), (2, 1))
        self.assertIn("rollout_failed", output)
        code, text, calls = self.call_cli(["report"])
        self.assertEqual((code, calls), (0, 0))
        self.assertEqual(json.loads(text)["rows"][0]["status"], "rollout_failed")

    def test_failed_resume_is_read_only_and_never_retries_or_judges(self):
        self.create_failure()
        before = {p.name: p.read_bytes() for p in self.failed.iterdir() if p.is_file()}
        with patch.object(self.sandbox, "run", side_effect=AssertionError("retry")):
            code, _, calls = self.call_cli(
                ["evaluate", "--harness", str(self.harness), "--max-tasks", "1"]
            )
        self.assertEqual((code, calls), (0, 0))
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.failed.iterdir() if p.is_file()}
        )

    def test_incomplete_run_is_unknown_error_not_zero_and_next_task_runs(self):
        self.failed.mkdir(parents=True)
        code, _, calls = self.evaluate()
        self.assertEqual(code, 3)
        self.assertEqual(self.rows()[0]["rollout"], "incomplete")
        self.assertIsNone(self.rows()[0]["score"])
        self.assertEqual((self.sandbox.calls, calls), (1, 1))
        code, text, _ = self.call_cli(["report"])
        self.assertEqual(code, 3)
        row = json.loads(text)["rows"][0]
        self.assertEqual(row["status"], "incomplete")
        self.assertIsNone(row["ground_truth"])

    def test_failed_record_substitution_or_changed_trace_refuses(self):
        self.create_failure()
        record = self.failed / "failed.json"
        original = record.read_bytes()
        for change in ("task", "harness", "calls"):
            with self.subTest(change=change):
                value = json.loads(original)
                if change == "calls":
                    value["model_calls"] = True
                else:
                    value["identity"][change] = "0" * 64
                record.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    self.evaluate()
        record.write_bytes(original)
        (self.failed / "pi.jsonl").write_text("changed")
        with self.assertRaises(ValueError):
            self.call_cli(["report"])

    def test_dual_terminal_records_are_not_a_failure_or_a_completed_resume(self):
        self.create_failure()
        (self.failed / "complete.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.evaluate()
        with self.assertRaises(ValueError):
            self.call_cli(["report"])

    def test_unrecorded_runtime_exception_still_aborts(self):
        with patch.object(self.sandbox, "run", side_effect=RuntimeError("infrastructure fault")):
            with self.assertRaisesRegex(RuntimeError, "infrastructure fault"):
                self.evaluate()

    def test_legacy_failure_needs_matching_launch_and_process_bytes(self):
        self.create_failure()
        path = self.failed / "failed.json"
        obj = json.loads(path.read_text())
        legacy = {key: obj[key] for key in ("identity", "process", "model_calls")}
        legacy["process"].update(
            stdout=(self.failed / "pi.jsonl").read_text(),
            stderr=(self.failed / "stderr.txt").read_text(),
        )
        path.write_text(json.dumps(legacy))
        self.assertEqual(runner.load_failed(self.failed)["reason"], "call cap reached at 32")
        (self.failed / "stderr.txt").write_text("substituted")
        with self.assertRaises(ValueError):
            runner.load_failed(self.failed)

    def test_context_error_keeps_the_broker_diagnostic_and_no_muse(self):
        class ContextSandbox(CapSandbox):
            def run(self, *args, **kwargs):
                result = super().run(*args, **kwargs)
                return ProcessResult(
                    result.returncode,
                    result.stdout.replace("429 status", "413 status"),
                    result.stderr,
                    result.seconds,
                    result.reason,
                )

        class ContextBroker(OfflineBroker):
            def __init__(self, *args, **kwargs):
                self.calls = 21
                self.last_failure = error_detail(413)

        self.sandbox = ContextSandbox([FAIL_TASK])
        self.adapter._runner = runner.Runner(self.sandbox, approved=True, _broker=ContextBroker)
        code, _, calls = self.call_cli(
            ["evaluate", "--harness", str(self.harness), "--max-tasks", "1"]
        )
        self.assertEqual((code, calls), (0, 0))
        row = self.rows()[0]
        self.assertEqual(row["model_calls"], 21)
        self.assertIn("HTTP 413", row["reason"])
        saved = runner.load_failed(self.failed)
        self.assertEqual(saved["broker_error"]["status"], 413)
        self.assertIn("HTTP 413", self.adapter.trajectory(self.failed, obs_trunc=1000))

    def test_forged_failure_reason_and_success_disguised_as_failure_refuse(self):
        self.create_failure()
        record = self.failed / "failed.json"
        saved = json.loads(record.read_text())
        saved["reason"] = "context full"
        record.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "reason contradicts"):
            runner.load_failed(self.failed)
        saved["reason"] = "call cap reached at 32"
        events = [
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "stop", "content": []},
            },
            {"type": "agent_settled", "aborted": False},
        ]
        stream = self.failed / "pi.jsonl"
        stream.write_text("\n".join(map(json.dumps, events)))
        saved["outputs"]["pi.jsonl"] = hashlib.sha256(stream.read_bytes()).hexdigest()
        record.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "successful terminal"):
            runner.load_failed(self.failed)

    def test_report_refuses_a_failure_without_task_and_harness_identity(self):
        self.create_failure()
        launch = self.failed / "launch.json"
        launch_obj = json.loads(launch.read_text())
        launch_obj["identity"] = {}
        launch.write_text(json.dumps(launch_obj))
        path = self.failed / "failed.json"
        saved = json.loads(path.read_text())
        saved["identity"] = {}
        saved["outputs"]["launch.json"] = hashlib.sha256(launch.read_bytes()).hexdigest()
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.call_cli(["report"])


class FailedContext(Fixture):
    def test_composer_and_reflection_get_failed_trajectory_and_reason(self):
        self.sandbox.fail_names = {"inv1"}
        seen = {}

        class Observer(OfflineImprover):
            def call(self, mission, context_dir, label, **kwargs):
                if label.startswith(("agg1.", "experience.")):
                    root = Path(context_dir)
                    seen[label] = (root / "pool/inv1/rollout.md").read_text()
                    if label.startswith("experience."):
                        seen["reflection-direct"] = (root / "rollouts/inv1.md").read_text()
                return super().call(mission, context_dir, label, **kwargs)

        prop = Observer(ChannelSpec("claude_cli", "offline"))
        library = Library(
            root=self.root / "library", domain="mojo", surface=self.adapter.substrate.surface()
        )
        with patch.object(self.adapter, "score", side_effect=AssertionError("grade in adaptation")):
            result = schedule.run_task(
                "train",
                1,
                adapter=self.adapter,
                prop=prop,
                runs_root=self.root / "adapt",
                modes=("agg",),
                selector_prop=prop,
                library=library,
            )
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(len(seen), 3)
        for text in seen.values():
            self.assertIn("COMPILE_ON_WRITE_ATTEMPT", text)
            self.assertIn("call cap reached at 32", text)
            self.assertNotIn("failed to read trajectory", text)
            self.assertNotIn("SECRET_REFERENCE", text)
            self.assertNotIn("SECRET_EXPECTATION", text)

    def test_a_failed_reference_keeps_its_evidence_next_to_successful_references(self):
        self.sandbox.fail_names = {"base1"}
        rolled = schedule._roll_bases(
            "train",
            self.adapter,
            [("base1", "refs/base1"), ("base2", "refs/base2")],
            self.root / "missing",
        )
        self.assertFalse(rolled["base1"].ok)
        self.assertTrue(rolled["base2"].ok)
        self.assertIn(
            "call cap reached at 32",
            self.adapter.trajectory(rolled["base1"].run_dir, obs_trunc=self.adapter.spec.obs_trunc),
        )


class BudgetMission(Fixture):
    def test_all_improver_stages_state_the_actual_executor_limits(self):
        bp, surface = self.adapter.spec.prompts, self.adapter.substrate.surface()
        missions = [prompts.propose(bp, dim, surface) for dim in ("INV", "DOM")]
        missions += [
            prompts.aggregate(bp, surface),
            prompts.compose_round(bp, surface, t=1, k=1),
            prompts.experience(bp),
        ]
        run = self.adapter.execute("zz-next", self.harness, "limits")
        launch = json.loads((run / "launch.json").read_text())
        provider = json.loads((run / "workspace/.agent/models.json").read_text())["providers"][
            "nomos"
        ]
        self.assertEqual(launch["max_model_calls"], 32)
        self.assertEqual(provider["models"][0]["contextWindow"], 32768)
        for text in missions:
            self.assertIn("32 admitted model calls", text)
            self.assertIn("32,768", text)
            self.assertIn("NOMOS_MAX_SEQ=32768", text)
            self.assertIn("not tool calls", text)
        self.assertEqual(
            prompts._work(BenchPrompts(work="Unchanged work")), "# THE WORK\n\nUnchanged work"
        )
