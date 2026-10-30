"""The Pi editable surface and the improver prompts rendered for it."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import prompts
from harness2.core.spec import BenchPrompts
from harness2.core.tree import autotrigger, read_tree, validate, write_tree
from harness2.substrate.pi import PI_SURFACE

TREE = {a: "" for a in PI_SURFACE.always_on} | {
    "guardrails.md": (
        "- When counting files, read the `count_files` skill's SKILL.md and follow it.\n"
    ),
    "skills/count_files/SKILL.md": (
        "---\nname: count_files\ndescription: Count files\n---\n"
        "Run `python3 skills/count_files/scripts/count.py`.\n"
    ),
    "skills/count_files/scripts/count.py": "print(1)\n",
    "scripts/check.py": "print('ok')\n",
    "extensions/guard.ts": "export default function (pi) {}\n",
}


class PiSurfaceTests(unittest.TestCase):
    def test_surface_values(self):
        self.assertEqual(PI_SURFACE.name, "pi")
        self.assertEqual(PI_SURFACE.plugin_dir, "extensions")
        self.assertIsNone(PI_SURFACE.hooks_file)
        self.assertEqual(PI_SURFACE.skill_dir, "skills")
        self.assertEqual(
            PI_SURFACE.always_on,
            ("systemprompt.md", "guardrails.md", "LongTermMEMORY.md", "tool_descriptions/tool_guidance.md"),
        )
        self.assertFalse(PI_SURFACE.reject_empty_always_on)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            d = write_tree(PI_SURFACE, TREE, Path(td) / "h")
            self.assertTrue((d / "extensions" / "README.md").is_file())  # stub written...
            self.assertEqual(read_tree(PI_SURFACE, d), TREE)  # ...but excluded from the tree

    def test_validate_accepts_tree(self):
        census = validate(PI_SURFACE, TREE, allow_subagents=False)
        self.assertEqual((census["plugins"], census["skills"], census["scripts"]), (1, 1, 1))

    def test_validate_rejects_subagent(self):
        tree = TREE | {"sub_agents/helper/AGENT.md": "---\ndescription: d\n---\nhelp\n"}
        with self.assertRaisesRegex(ValueError, "sub_agents are not available"):
            validate(PI_SURFACE, tree, allow_subagents=False)

    def test_validate_rejects_untriggered_skill(self):
        tree = dict(TREE)
        tree["guardrails.md"] = ""
        with self.assertRaisesRegex(ValueError, "count_files"):
            validate(PI_SURFACE, tree, allow_subagents=False)

    def test_autotrigger_line_format(self):
        tree = dict(TREE)
        tree["guardrails.md"] = ""
        self.assertEqual(autotrigger(PI_SURFACE, tree), ["count_files"])
        self.assertTrue(
            tree["guardrails.md"].endswith("read the `count_files` skill's SKILL.md and follow it.\n")
        )
        self.assertIn("When Count files,", tree["guardrails.md"])


class PiPromptTests(unittest.TestCase):
    def test_prompts_render_for_pi(self):
        adapt = BenchPrompts(work="w")
        tts = BenchPrompts(work="w", prompt_pack="tts")
        texts = (
            prompts.propose(adapt, "INV", PI_SURFACE),
            prompts.propose(adapt, "DOM", "pi", round_t=1),
            prompts.propose(tts, "DOM", "pi"),
            prompts.aggregate(tts, "pi"),
            prompts.aggregate(adapt, "pi"),
            prompts.compose_round(adapt, "pi", t=1, k=1),
        )
        for text in texts:
            self.assertIn("extensions/<name>.ts", text)
            self.assertIn("tool_call", text)
            for bad in ("plugin/<name>.ts", ".codex/hooks.json", "OpenCode", "Jinja", "`task` tool"):
                self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
