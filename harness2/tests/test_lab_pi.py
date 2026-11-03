"""LAB on the Pi substrate: spec, adapter wiring, base harness, validation, launch, preflight."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import lab
from harness2.benchmarks.lab import LabAdapter, lab_spec
from harness2.core.tree import read_tree
from harness2.substrate.base import Install, RunRequest
from harness2.substrate.pi import PI_SURFACE, PiSubstrate
from tests.test_lab_pi_runner import PREAMBLE, fake_lab

FIXTURE = Path(__file__).parent / "fixtures" / "pi_events_smoke.jsonl"


class LabPiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.deps = self.td / "deps"
        self.lab_root = fake_lab(self.deps / "harvey-labs")
        for p in (
            patch.object(config, "DEPENDENCIES", self.deps),
            patch.object(config, "RUNS", self.td / "runs"),
            patch.object(config, "LAB_PYTHON", self.lab_root / ".venv" / "bin" / "python"),
            patch.object(config, "SOLVER_MODEL_PI", "zai/glm-5.3-flash"),
            patch.object(config, "PI_AGENT_DIR", self.td / "agent"),
        ):
            p.start()
            self.addCleanup(p.stop)
        lab._BASE_DONE.clear()

    def _adapter(self) -> LabAdapter:
        return LabAdapter(substrate="pi")

    def test_lab_spec_pi(self):
        s = lab_spec("pi")
        self.assertEqual(s.install_mode, "pi_workspace")
        self.assertIn("pack.py", s.reserved_scripts)
        self.assertFalse(s.allow_subagents)

    def test_adapter_pi_wiring(self):
        a = self._adapter()
        self.assertIsInstance(a.substrate, PiSubstrate)
        self.assertEqual(a.model, "zai/glm-5.3-flash")
        self.assertTrue(str(a._results_root).endswith("results/pi"))
        self.assertEqual(a.substrate.name, "pi")

    def test_base_harness_pi_composes_preamble_and_delta(self):
        tree = read_tree(PI_SURFACE, self._adapter().base_harness())
        self.assertTrue(tree["systemprompt.md"].startswith(PREAMBLE.strip()))
        self.assertIn("output/", tree["systemprompt.md"])
        self.assertIn("read_doc.py", tree["systemprompt.md"])
        for name in ("guardrails.md", "LongTermMEMORY.md", "tool_descriptions/tool_guidance.md"):
            self.assertEqual(tree[name], "")

    def test_validate_pi_accepts_base_and_rejects_missing_markers(self):
        a = self._adapter()
        base = read_tree(PI_SURFACE, a.base_harness())
        a.validate_tree(base)
        bad = dict(base)
        bad["systemprompt.md"] = "hello"
        with self.assertRaisesRegex(ValueError, "output/"):
            a.validate_tree(bad)

    def test_validate_pi_rejects_base_skill_name(self):
        a = self._adapter()
        tree = read_tree(PI_SURFACE, a.base_harness())
        tree["skills/docx/SKILL.md"] = "---\nname: docx\ndescription: d\n---\nbody\n"
        tree["guardrails.md"] = "- When editing Word files, read the `docx` skill's SKILL.md and follow it.\n"
        with self.assertRaisesRegex(ValueError, "docx"):
            a.validate_tree(tree)

    def test_launch_pi_argv(self):
        a = self._adapter()
        rendered = self.td / "_rendered"
        rendered.mkdir()
        install = Install(staging_dir=self.td, fingerprint="f", extras={"harness": "staged", "rendered": str(rendered)})
        req = RunRequest(task_ref="employment-labor/t1", run_ids=("lab-pi-agg1/employment-labor/t1/base1",), timeout=100)
        with patch.object(lab, "run_detached") as run:
            dirs = a._launch_pi(req, install)
        self.assertEqual(dirs, [a._run_dir("lab-pi-agg1/employment-labor/t1/base1")])
        argv = run.call_args.args[0]
        kw = run.call_args.kwargs
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[argv.index("-m") + 1], "harness2.benchmarks.lab_pi_runner")
        self.assertEqual(argv[argv.index("--harness-dir") + 1], str(rendered))
        self.assertEqual(argv[argv.index("--lab-root") + 1], str(config.lab_repo()))
        self.assertEqual(argv[argv.index("--agent-dir") + 1], str(self.td / "agent"))
        self.assertEqual(argv[argv.index("--model") + 1], "zai/glm-5.3-flash")
        self.assertEqual(argv[argv.index("--timeout") + 1], "100")
        self.assertEqual(argv[argv.index("--doc-parser") + 1], "host")
        self.assertEqual(kw["timeout"], 800)
        self.assertEqual(kw["env"]["PYTHONPATH"], str(config.lab_repo()))

    def test_install_base_is_rendered_on_pi(self):
        a = self._adapter()
        inst = a._install(None)
        self.addCleanup(lambda: shutil.rmtree(inst.staging_dir, ignore_errors=True))
        self.assertEqual(inst.extras["harness"], "staged")
        append = Path(inst.extras["rendered"]) / ".harness2" / "APPEND_SYSTEM.md"
        self.assertTrue(append.is_file())
        self.assertTrue(append.read_text().startswith(PREAMBLE.strip()))

    def test_trajectory_reads_pi_session(self):
        run_dir = self.td / "r"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "pi_session.jsonl")
        self.assertIn("[ACTION]", self._adapter().trajectory(run_dir, obs_trunc=1000))
        self.assertIn("no trajectory", self._adapter().trajectory(self.td, obs_trunc=1000))

    def test_preflight_pi_requires_pandoc(self):
        a = self._adapter()
        with patch.object(a.substrate, "preflight"), patch.object(
            lab.shutil, "which", lambda b: None if b == "pandoc" else f"/usr/bin/{b}"
        ):
            with self.assertRaisesRegex(RuntimeError, "pandoc"):
                a.preflight()

    def test_preflight_pi_requires_runtime_scripts(self):
        shutil.rmtree(self.lab_root / "harness" / "skills" / "docx" / "scripts")
        a = self._adapter()
        with patch.object(a.substrate, "preflight"):
            with self.assertRaisesRegex((RuntimeError, FileNotFoundError), "setup"):
                a.preflight()


if __name__ == "__main__":
    unittest.main()
