"""Sampling presets for Glimmer's Mojo rollouts, for repeated sampled held-out evaluation."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import mojo_pi_runner as R
from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_tasks import Corpus
from harness2.substrate.sandbox import ProcessResult

# The identity fields runs 1 and 2 recorded; a greedy rollout records exactly these, and no sampling field.
GREEDY_IDENTITY_KEYS = {"task", "harness", "model", "temperature", "thinking", "sandbox", "runtime", "runner_sha256",
                        "budgets"}
VENDOR = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


class FakePiSandbox:
    def identity(self):
        return {"offline": True}

    def run(self, work, args, **kw):
        (work / "solution.mojo").write_text("def main():\n    print(7)\n")
        events = [
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "Saved."}]},
            },
            {"type": "agent_settled", "aborted": False},
        ]
        return ProcessResult(0, "\n".join(json.dumps(e) for e in events), "", 0)


class SamplingPresets(unittest.TestCase):
    def test_default_preset_is_greedy(self):
        with patch.dict(os.environ, {}):
            os.environ.pop("HARNESS2_MOJO_SAMPLING", None)
            self.assertEqual(R.sampling_from_env(), "greedy")

    def test_unknown_preset_is_refused(self):
        with patch.dict(os.environ, {"HARNESS2_MOJO_SAMPLING": "hot"}):
            with self.assertRaisesRegex(ValueError, "HARNESS2_MOJO_SAMPLING"):
                R.sampling_from_env()

    def test_presets_are_temperature_zero_and_glimmers_defaults(self):
        self.assertEqual(R.SAMPLING_PRESETS["greedy"], {"temperature": 0})
        self.assertEqual(R.SAMPLING_PRESETS["vendor"], VENDOR)

    def test_models_json_carries_the_preset(self):
        for name in ("greedy", "vendor"):
            with tempfile.TemporaryDirectory() as d:
                R.prepare_agent(Path(d), name)
                models = json.loads((Path(d) / ".agent/models.json").read_text())
                self.assertEqual(models["providers"]["nomos"]["models"][0]["samplingParams"], R.SAMPLING_PRESETS[name])


class SampledRollouts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-sampling-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        train, test = self.root / "train", self.root / "test"
        train.write_text(json.dumps({"id": "train", "prompt": "write a function", "kind": "run", "expect_stdout": "7"}))
        test.write_text(json.dumps({"id": "held", "prompt": "unseen", "test": 'def main():\n    print("ok")', "reference": "x"}))
        sandbox = FakePiSandbox()
        self.adapter = MojoAdapter(
            corpus=Corpus(train, test), runs_root=self.root / "runs",
            runner=R.Runner(sandbox, approved=True), sandbox=sandbox,
        )

    def identity(self, run):
        return json.loads((run / "complete.json").read_text())["identity"]

    def test_greedy_rollout_identity_is_unchanged(self):
        with patch.dict(os.environ, {}):
            os.environ.pop("HARNESS2_MOJO_SAMPLING", None)
            ident = self.identity(self.adapter.execute("train", None, "greedy"))
        self.assertEqual(set(ident), GREEDY_IDENTITY_KEYS)
        self.assertEqual(ident["temperature"], 0)

    def test_vendor_rollout_records_its_sampling(self):
        with patch.dict(os.environ, {"HARNESS2_MOJO_SAMPLING": "vendor"}):
            run = self.adapter.execute("train", None, "vendor")
        ident = self.identity(run)
        self.assertEqual(ident["temperature"], 1.0)
        self.assertEqual(ident["sampling"], {"preset": "vendor", **VENDOR})
        models = json.loads((run / "workspace/.agent/models.json").read_text())
        self.assertEqual(models["providers"]["nomos"]["models"][0]["samplingParams"], VENDOR)

    def test_a_greedy_rollout_never_resumes_as_vendor(self):
        with patch.dict(os.environ, {}):
            os.environ.pop("HARNESS2_MOJO_SAMPLING", None)
            self.adapter.execute("train", None, "same")
        with patch.dict(os.environ, {"HARNESS2_MOJO_SAMPLING": "vendor"}):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.adapter.execute("train", None, "same")


if __name__ == "__main__":
    unittest.main()
