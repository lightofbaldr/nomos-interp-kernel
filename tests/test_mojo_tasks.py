import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_tasks import Corpus
from harness2.cli import run_sweep


class Tasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.train = self.root / "train.jsonl"
        self.test = self.root / "test.jsonl"
        self.write(
            self.train,
            [
                {"id": f"train{i}", "kind": "run", "prompt": f"print {i}", "expect_stdout": str(i)}
                for i in range(8)
            ],
        )
        self.write(
            self.test,
            [
                {
                    "id": "held",
                    "prompt": "implement x",
                    "test": 'def main():\n    print("ok")',
                    "reference": "DO NOT SHOW ME",
                }
            ],
        )

    def write(self, path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_both_schemas_and_no_reference_in_executor(self):
        c = Corpus(self.train, self.test)
        self.assertEqual(len(c.ids("train")), 8)
        self.assertEqual(c.ids("test"), ["held"])
        t = c.tasks["held"]
        self.assertNotIn("DO NOT SHOW ME", repr(t))
        self.assertNotIn('print("ok")', t.instructions)
        self.assertIn("Do not define main", t.instructions)

    def test_seeded_selection_is_reproducible_and_bound(self):
        a = Corpus(self.train, self.test, counts={"train": 4, "test": 1}, seed=7)
        b = Corpus(self.train, self.test, counts={"train": 4, "test": 1}, seed=7)
        self.assertEqual(a.identity, b.identity)
        self.assertEqual(len(a.ids("train")), 4)
        self.assertEqual(a.identity["seed"], 7)

    def test_heldout_wins_duplicate_id(self):
        self.write(
            self.train,
            [
                {"id": "held", "kind": "compile", "prompt": "same id"},
                {"id": "new", "kind": "compile", "prompt": "different"},
            ],
        )
        c = Corpus(self.train, self.test)
        self.assertEqual(c.ids("train"), ["new"])
        self.assertIn("train:held", c.identity["exclusions"])

    def test_bad_ids_schemas_and_duplicates_refuse(self):
        good = {"id": "a", "kind": "run", "prompt": "x", "expect_stdout": "x"}
        for rows in (
            [good, good],
            [good | {"id": "../x"}],
            [good | {"expect_stdout": None}],
            [good | {"kind": "other"}],
            [good | {"test": "x"}],
        ):
            with self.subTest(rows=rows):
                self.write(self.train, rows)
                with self.assertRaises(ValueError):
                    Corpus(self.train, self.test)

    def test_selection_cannot_move_heldout(self):
        select = self.root / "select.json"
        select.write_text(json.dumps({"train": ["held"], "test": ["train0"]}))
        with self.assertRaises(ValueError):
            Corpus(self.train, self.test, select)

    def test_dry_run_no_subprocess_no_credential_no_runtime(self):
        adapter = MojoAdapter(corpus=Corpus(self.train, self.test), runs_root=self.root / "runs")
        with (
            patch("harness2.cli._common.make_adapter", return_value=adapter),
            patch.object(adapter, "runtime", side_effect=AssertionError("runtime")),
            patch("harness2.core.muse_judge.key_from_file", side_effect=AssertionError("secret")),
        ):
            self.assertEqual(
                run_sweep.main(
                    ["--bench", "mojo", "--substrate", "pi", "--split", "train", "--dry-run"]
                ),
                0,
            )
        self.assertFalse((self.root / "runs").exists())

    def test_live_and_heldout_adaptation_gates(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                run_sweep.main(["--bench", "mojo", "--substrate", "pi", "--split", "train"]), 1
            )
        with patch.dict("os.environ", {"HARNESS2_MOJO_LIVE_APPROVED": "1"}):
            self.assertEqual(
                run_sweep.main(["--bench", "mojo", "--substrate", "pi", "--split", "test"]), 1
            )

    def test_simd_is_excluded_and_only_runnable_smoke(self):
        smoke = self.root / "smoke.jsonl"
        self.write(
            smoke,
            [
                {"id": "simd_dot", "kind": "run", "prompt": "contradiction", "expect_stdout": "30"},
                {"id": "smoke", "kind": "run", "prompt": "print smoke", "expect_stdout": "smoke"},
                {"id": "compile_only", "kind": "compile", "prompt": "compilation"},
            ],
        )
        c = Corpus(self.train, self.test, smoke=smoke, counts={"train": 4, "test": 1, "smoke": 1})
        self.assertEqual(c.ids("smoke"), ["smoke"])
        self.assertNotIn("simd_dot", c.tasks)
