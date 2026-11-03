# Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md.
"""Contracts of the shared headless Claude Code / Codex tooling."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core.clitools import (
    SYSTEM_ARG_MAX,
    classify_failure,
    claude_argv,
    claude_env,
    claude_system_args,
    codex_argv,
    codex_system_block,
    parse_claude_result,
    parse_codex_result,
    retry_after_seconds,
    strip_nested_env,
)


class EnvAndArgvTests(unittest.TestCase):
    def test_strip_nested_env(self):
        out = strip_nested_env(
            {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "7", "PATH": "/x"}
        )
        self.assertEqual(out, {"PATH": "/x"})

    def test_claude_auth_isolation_does_not_mutate_or_change_other_children(self):
        parent = {"ANTHROPIC_API_KEY": "fake-parent-key", "CLAUDECODE": "1", "PATH": "/x"}
        before = dict(parent)
        self.assertEqual(claude_env(parent), {"PATH": "/x"})
        expected_api = {"ANTHROPIC_API_KEY": "fake-parent-key", "PATH": "/x"}
        self.assertEqual(claude_env(parent, isolation="bare"), expected_api)
        self.assertEqual(strip_nested_env(parent), expected_api)
        self.assertEqual(parent, before)

    def test_claude_argv_flags_isolation(self):
        argv = claude_argv(model="opus", effort="high", system="S", skip_permissions=True)
        self.assertEqual(argv[1:4], ["-p", "--output-format", "json"])
        for flag in (
            "--dangerously-skip-permissions",
            "--setting-sources",
            "--strict-mcp-config",
            "--disable-slash-commands",
        ):
            self.assertIn(flag, argv)
        self.assertNotIn("--bare", argv)
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "S")
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_claude_argv_bare_only_when_asked(self):
        self.assertIn("--bare", claude_argv(model="opus", effort="low", isolation="bare"))
        argv = claude_argv(model="opus", effort="low", tools="", json_schema='{"type":"object"}')
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--json-schema") + 1], '{"type":"object"}')

    def test_claude_argv_tools_never_last(self):
        # `--tools` is variadic in claude: if it sat last it would swallow a positional prompt.
        argv = claude_argv(model="opus", effort="low", tools="")
        self.assertNotEqual(argv[-2], "--tools")
        self.assertEqual(argv[argv.index("--tools") + 1], "")

    def test_codex_argv(self):
        argv = codex_argv(
            model="gpt-5.6-sol", effort="high", cwd=Path("/w"), last_message=Path("/w/last.txt")
        )
        self.assertEqual(argv[-1], "-")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("-o") + 1], "/w/last.txt")
        self.assertEqual(argv[argv.index("-C") + 1], "/w")
        ro = codex_argv(
            model="m",
            effort="low",
            cwd=Path("/w"),
            last_message=Path("/l"),
            sandbox="read-only",
            images=[Path("/i.png")],
            schema_file=Path("/s.json"),
        )
        self.assertEqual(ro[ro.index("-s") + 1], "read-only")
        self.assertEqual(ro[ro.index("-i") + 1], "/i.png")
        self.assertEqual(ro[ro.index("--output-schema") + 1], "/s.json")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", ro)


class ResultParsingTests(unittest.TestCase):
    def test_parse_claude_result(self):
        ok = parse_claude_result(
            '{"result":"hi","total_cost_usd":0.5,"subtype":"success","usage":{"input_tokens":3}}',
            0,
            "",
        )
        self.assertEqual((ok.text, ok.cost_usd, ok.usage["input_tokens"], ok.ok), ("hi", 0.5, 3, True))
        bad = parse_claude_result(
            '{"is_error":true,"result":"You have reached your usage limit. Resets at 4pm",'
            '"subtype":"error"}',
            1,
            "",
        )
        self.assertEqual((bad.ok, bad.limited), (False, True))
        structured = parse_claude_result(
            '{"result":"x","structured_output":{"verdict":"pass"},"subtype":"success"}', 0, ""
        )
        self.assertEqual(json.loads(structured.text), {"verdict": "pass"})
        dead = parse_claude_result("", 1, "boom: not logged in")
        self.assertFalse(dead.ok)
        self.assertIn("not logged in", dead.error)

    def test_parse_claude_result_success_is_never_limited(self):
        # A CLI-internal retry notice on stderr must not turn a paid, successful verdict into
        # a 429: `limited` is a property of failures only.
        ok = parse_claude_result(
            '{"result":"{\\"verdict\\":\\"pass\\"}","subtype":"success"}',
            0,
            "API Error (429 rate_limit_error) · Retrying in 2 seconds…",
        )
        self.assertTrue(ok.ok)
        self.assertFalse(ok.limited)
        stream_ok = parse_codex_result(Path("/nonexistent"), '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"fine"}}\n', 0, "429 Too Many Requests earlier, recovered")
        self.assertTrue(stream_ok.ok)
        self.assertFalse(stream_ok.limited)

    def test_claude_system_args_fallback(self):
        self.assertEqual(claude_system_args("S", "M"), ("S", "M"))
        big = "x" * (SYSTEM_ARG_MAX + 1)
        system_arg, stdin = claude_system_args(big, "M")
        self.assertLess(len(system_arg), 200)
        self.assertTrue(stdin.startswith("# SYSTEM INSTRUCTIONS"))
        self.assertTrue(stdin.endswith("# END\n\nM"))
        self.assertEqual(claude_system_args("", "M"), ("", "M"))

    def test_parse_codex_result_prefers_last_message_file(self):
        with tempfile.TemporaryDirectory() as td:
            last = Path(td) / "last.txt"
            last.write_text("done", encoding="utf-8")
            stdout = (
                '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2,'
                '"cached_input_tokens":0,"reasoning_output_tokens":1}}\n'
            )
            r = parse_codex_result(last, stdout, 0, "")
        self.assertEqual((r.text, r.usage["input_tokens"], r.ok), ("done", 10, True))

    def test_parse_codex_result_falls_back_to_stream(self):
        stdout = (
            '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"from stream"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
        )
        r = parse_codex_result(Path("/nonexistent/last.txt"), stdout, 0, "")
        self.assertEqual((r.text, r.ok), ("from stream", True))

    def test_parse_codex_result_reports_turn_failure(self):
        stdout = '{"type":"turn.failed","error":{"message":"usage limit reached, retry after 120 seconds"}}\n'
        r = parse_codex_result(Path("/nonexistent/last.txt"), stdout, 1, "")
        self.assertFalse(r.ok)
        self.assertIn("usage limit", r.error)
        self.assertTrue(r.limited)


class FailureClassificationTests(unittest.TestCase):
    def test_classify_failure_samples(self):
        for s in ("usage limit reached", "You've hit your limit", "weekly limit", "quota exceeded"):
            self.assertEqual(classify_failure(s), "usage_limit", s)
        for s in ("rate_limit_error", "429 Too Many Requests", "overloaded_error", "Rate limit"):
            self.assertEqual(classify_failure(s), "rate_limit", s)
        for s in ("Your limit resets at 3pm", "resets in 2 hours", "You've hit your limit · resets 3pm"):
            self.assertEqual(classify_failure(s), "usage_limit", s)
        self.assertIsNone(classify_failure("SyntaxError in extension"))
        self.assertEqual(retry_after_seconds("retry after 90 seconds"), 90)
        self.assertEqual(retry_after_seconds("try again in 45 seconds"), 45)
        self.assertIsNone(retry_after_seconds("no hint"))

    def test_codex_system_block(self):
        block = codex_system_block("S", "M")
        self.assertTrue(block.startswith("# SYSTEM INSTRUCTIONS\n\n"))
        self.assertTrue(block.endswith("# END\n\nM"))


if __name__ == "__main__":
    unittest.main()
