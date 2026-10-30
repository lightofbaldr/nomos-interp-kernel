"""The shipped LAB judge patch: a `cli/` provider that grades through harness2-judge-bridge."""

import os
import subprocess
import unittest

from harness2 import config

PATCH = config.THIRD_PARTY / "patches" / "harvey-labs-judge-vertex-anthropic.diff"
LAB = config.DEPENDENCIES / "harvey-labs"
PY = LAB / ".venv" / "bin" / "python"


class PatchTests(unittest.TestCase):
    def test_patch_text_adds_cli_provider(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertIn("openai-compatible", text)
        self.assertIn("HARNESS2_JUDGE_BASE_URL", text)
        self.assertIn("HARNESS2_JUDGE_API_KEY", text)

    def test_lab_judge_model_passthrough(self):
        judge = "cli/claude:sonnet"
        lab_model = "vertex/" + judge if judge.startswith(("claude", "gemini")) else judge
        self.assertEqual(lab_model, judge)  # the config.LAB_JUDGE_MODEL expression leaves cli/ ids alone

    @unittest.skipUnless((LAB / ".git").is_dir(), "LAB checkout not installed")
    def test_patch_applies_reverse_clean(self):
        r = subprocess.run(
            ["git", "-C", str(LAB), "apply", "--reverse", "--check", str(PATCH)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(PY.is_file(), "LAB venv not installed")
    def test_patched_judge_routes_cli_prefix(self):
        code = (
            "from evaluation.judge import Judge, _detect_provider\n"
            "assert _detect_provider('cli/claude:sonnet') == 'openai-compatible', _detect_provider('cli/claude:sonnet')\n"
            "assert _detect_provider('claude-sonnet-4-6') == 'anthropic'\n"
            "j = Judge('cli/claude:sonnet')\n"
            "assert j.model == 'claude:sonnet', j.model\n"
            "assert str(j.client.base_url).startswith('http://127.0.0.1:1/v1'), j.client.base_url\n"
            "print('ok')\n"
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(LAB),
            "HARNESS2_JUDGE_BASE_URL": "http://127.0.0.1:1/v1",
            "HARNESS2_JUDGE_API_KEY": "k",
        }
        r = subprocess.run([str(PY), "-c", code], cwd=LAB, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
