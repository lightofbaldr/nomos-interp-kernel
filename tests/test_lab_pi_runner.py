"""The LAB Pi runner: task loading, workspace staging, invocation, the nudge loop, metrics."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.benchmarks import lab_pi_runner as L
from harness2.core.tree import write_tree
from harness2.substrate.pi import PI_SURFACE, render_pi_workspace
from tests.test_pi_surface import TREE

PREAMBLE = "You are an AI agent executing a task within a workspace.\n"


def fake_lab(root: Path, *, inline_instructions: bool = True) -> Path:
    """A minimal harvey-labs checkout: one task, one base skill, the runtime reader."""
    task = root / "tasks" / "employment-labor" / "t1"
    (task / "documents").mkdir(parents=True)
    (task / "documents" / "a.docx").write_bytes(b"PK\x03\x04docx")
    cfg = {"title": "T", "criteria": [{"id": "c1", "title": "x", "match_criteria": "y"}]}
    if inline_instructions:
        cfg["instructions"] = "Draft the markup."
    else:
        (task / "instructions.md").write_text("Draft the markup from file.")
    (task / "task.json").write_text(json.dumps(cfg))
    (root / "harness").mkdir()
    (root / "harness" / "system_prompt.md").write_text(PREAMBLE)
    skill = root / "harness" / "skills" / "docx"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: docx\ndescription: Word documents\n---\nRun `python skills/docx/scripts/pack.py`.\n")
    (skill / "scripts" / "pack.py").write_text("print('pack')\n")
    (root / "opencode" / "scripts").mkdir(parents=True)
    (root / "opencode" / "scripts" / "read_doc.py").write_text("# runtime reader\n")
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("")
    return root


def stream_lines(text: str, *, tool_calls: int = 1, mention: str = "", settled: bool = True) -> str:
    """A minimal `pi --mode json` stream: one assistant turn with tool calls, then settled."""
    content = [{"type": "text", "text": text}] + [
        {"type": "toolCall", "id": f"c{i}", "name": "bash", "arguments": {"command": f"cat {mention}"}}
        for i in range(tool_calls)
    ]
    lines = [
        {"type": "message_end", "message": {"role": "assistant", "content": content,
                                            "usage": {"input": 10, "output": 5, "reasoning": 1, "cost": {"total": 0.001}}}},
    ]
    for i in range(tool_calls):
        lines.append({"type": "tool_execution_end", "toolCallId": f"c{i}", "toolName": "bash",
                      "result": {"content": [{"type": "text", "text": "ok"}]}, "isError": False})
    lines.append({"type": "turn_end", "message": {}, "toolResults": []})
    if settled:
        lines.append({"type": "agent_settled", "aborted": False})
    return "".join(json.dumps(ln) + "\n" for ln in lines)


class FakePi:
    """Answers each invocation from a queue of (stream_text, files_to_write, write_session)."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        text, files, write_session, *rest = self.steps.pop(0) if self.steps else ("", {}, False)
        rc = rest[0] if rest else 0
        kw["stdout"].write(text)
        cwd = Path(kw["cwd"])
        for rel, body in files.items():
            p = cwd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        if write_session:
            sdir = Path(argv[argv.index("--session-dir") + 1])
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "2026-10-20T00-00-00-000Z_abc.jsonl").write_text('{"type":"session"}\n')
        return SimpleNamespace(returncode=rc)


class RunnerUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.lab = fake_lab(self.td / "lab")
        tree = write_tree(PI_SURFACE, TREE, self.td / "tree")
        self.render = self.td / "render"
        render_pi_workspace(tree, self.render, {})
        self.args = SimpleNamespace(
            task="employment-labor/t1", run_id="employment-labor/t1/base1", results_root=self.td / "results",
            model="zai/glm-5.3-flash", thinking="high", tools="+grep,+find,+ls", timeout=3600,
            harness_dir=str(self.render), agent_dir=self.td / "agent", pi_bin="pi",
            lab_root=self.lab, doc_parser="host", max_continue=2,
        )

    def test_load_task_reads_inline_or_file_instructions(self):
        t = L.load_task(self.lab, "employment-labor/t1")
        self.assertEqual((t["name"], t["instructions"]), ("employment-labor/t1", "Draft the markup."))
        self.assertTrue(Path(t["docs_dir"]).is_dir())
        lab2 = fake_lab(self.td / "lab2", inline_instructions=False)
        self.assertEqual(L.load_task(lab2, "employment-labor/t1")["instructions"], "Draft the markup from file.")
        with self.assertRaises(FileNotFoundError):
            L.load_task(self.lab, "employment-labor/missing")
        with self.assertRaises(ValueError):
            L.load_task(self.lab, "nope")

    def test_base_skill_dirs(self):
        self.assertEqual([p.name for p in L.base_skill_dirs(self.lab)], ["docx"])

    def test_build_prompt_sections(self):
        ws = Path("/w")
        p = L.build_prompt("Do it.", ws)
        self.assertTrue(p.startswith("=== WORKSPACE ===\n/w\n"))
        self.assertIn("=== DOCUMENTS (read-only) ===\n/w/documents", p)
        self.assertIn("=== OUTPUT DIRECTORY (deliverables go here) ===\n/w/output", p)
        self.assertTrue(p.endswith("=== ASSIGNMENT ===\nDo it."))
        self.assertIn("output/", L.ENV_DELTA_PI)
        self.assertIn("read_doc.py", L.ENV_DELTA_PI)
        self.assertEqual(L.NUDGE.split()[-1], "DONE")

    def test_stage_workspace_layout(self):
        run_dir = self.td / "results" / "r1"
        staged = L.stage_workspace(run_dir, L.load_task(self.lab, "employment-labor/t1"), self.lab, self.render)
        ws = staged.workspace
        self.assertEqual(ws, run_dir / "workspace")
        doc = ws / "documents" / "a.docx"
        self.assertTrue(doc.is_file())
        self.assertEqual(stat.S_IMODE(doc.stat().st_mode), 0o444)
        self.assertTrue((ws / "output").is_dir())
        self.assertTrue((ws / ".pi" / "skills" / "docx" / "SKILL.md").is_file())
        self.assertTrue((ws / "skills" / "docx" / "scripts" / "pack.py").is_file())
        self.assertTrue((ws / "skills" / "count_files" / "scripts" / "count.py").is_file())
        self.assertTrue((ws / ".harness2" / "APPEND_SYSTEM.md").is_file())
        self.assertTrue((ws / ".pi" / "extensions" / "guard.ts").is_file())
        self.assertTrue((ws / "scripts" / "check.py").is_file())
        self.assertEqual([s.name for s in staged.rendered.skills], ["docx", "count_files"])
        self.assertEqual([s.name for s in staged.base_skills], ["docx"])
        self.assertEqual((ws / "scripts" / "read_doc.py").read_text(), "# runtime reader\n")

    def test_stage_workspace_runtime_read_doc_wins(self):
        (self.render / "scripts" / "read_doc.py").write_text("evil")
        staged = L.stage_workspace(self.td / "results" / "r2", L.load_task(self.lab, "employment-labor/t1"), self.lab, self.render)
        self.assertEqual((staged.workspace / "scripts" / "read_doc.py").read_text(), "# runtime reader\n")

    def test_stage_workspace_without_harness(self):
        staged = L.stage_workspace(self.td / "results" / "r3", L.load_task(self.lab, "employment-labor/t1"), self.lab, None)
        self.assertIsNone(staged.rendered.append_system)
        self.assertEqual([s.name for s in staged.rendered.skills], ["docx"])

    def test_rollout_env(self):
        ws = self.td / "ws"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk", "PATH": "/usr/bin", "HOME": "/home/u"}):
            env = L.rollout_env(self.args, ws)
        self.assertTrue(env["PATH"].startswith(str(self.lab / ".venv" / "bin") + ":"))
        self.assertEqual(env["LAB_DOC_PARSER"], "host")
        self.assertEqual(env["LAB_BENCH_ROOT"], str(self.lab))
        self.assertEqual(env["WORKSPACE_DIR"], str(ws))
        self.assertEqual(env["DOCUMENTS_DIR"], str(ws / "documents"))
        self.assertEqual(env["OUTPUT_DIR"], str(ws / "output"))
        self.assertEqual(env["HARNESS2_WORKSPACE_ROOT"], str(ws))
        self.assertEqual(env["PI_CODING_AGENT_DIR"], str(self.td / "agent"))
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_pi_command_guard_first_and_resume(self):
        staged = L.stage_workspace(self.td / "results" / "r4", L.load_task(self.lab, "employment-labor/t1"), self.lab, self.render)
        argv = L.pi_command(self.args, staged.workspace, "p", staged.rendered, self.td / "s")
        self.assertEqual(argv[:3], ["timeout", "--kill-after=30s", "3600"])
        self.assertTrue(argv[argv.index("-e") + 1].endswith("pi_guard.ts"))
        self.assertEqual(argv.count("-e"), 2)
        self.assertEqual(argv.count("--skill"), 2)
        self.assertNotIn("--session", argv)
        self.assertEqual(argv[-2:], ["--", "p"])
        resumed = L.pi_command(self.args, staged.workspace, L.NUDGE, staged.rendered, self.td / "s", resume=Path("/s/x.jsonl"), timeout=120)
        self.assertEqual(resumed[2], "120")
        self.assertLess(resumed.index("--session"), resumed.index("--"))
        self.assertEqual(resumed[resumed.index("--session") + 1], "/s/x.jsonl")


class RunTaskTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.lab = fake_lab(self.td / "lab")
        tree = write_tree(PI_SURFACE, TREE, self.td / "tree")
        self.render = self.td / "render"
        render_pi_workspace(tree, self.render, {})
        self.args = SimpleNamespace(
            task="employment-labor/t1", run_id="employment-labor/t1/base1", results_root=self.td / "results",
            model="zai/glm-5.3-flash", thinking="high", tools="+grep,+find,+ls", timeout=3600,
            harness_dir=str(self.render), agent_dir=self.td / "agent", pi_bin="pi",
            lab_root=self.lab, doc_parser="host", max_continue=2,
        )
        self.run_dir = self.td / "results" / "employment-labor" / "t1" / "base1"

    def _run(self, *steps):
        fake = FakePi(*steps)
        with patch.object(L.subprocess, "run", fake):
            rc = L.run_task(self.args)
        return rc, fake

    def test_run_task_writes_layout_and_metrics_last(self):
        rc, fake = self._run((stream_lines("done", tool_calls=2, mention="a.docx"), {"output/x.docx": "x"}, True))
        self.assertEqual(rc, 0)
        for rel in ("config.json", "pi_session.jsonl", "output/x.docx", "metrics.json", "pi_stderr.log"):
            self.assertTrue((self.run_dir / rel).is_file(), rel)
        m = json.loads((self.run_dir / "metrics.json").read_text())
        self.assertEqual((m["runner"], m["turn_count"], m["tool_calls"], m["input_tokens"]), ("pi", 1, 2, 10))
        self.assertEqual((m["documents_read"], m["total_documents"]), (1, 1))
        self.assertTrue(m["finished_cleanly"])
        self.assertEqual(json.loads((self.run_dir / "config.json").read_text())["runner"], "pi")
        # Delivered but the final text was not DONE: one nudge, which adds no tool calls, then stop.
        self.assertEqual(len(fake.calls), 2)

    def test_run_task_without_events_writes_no_metrics(self):
        rc, _ = self._run(("", {}, False))
        self.assertEqual(rc, 2)
        self.assertFalse((self.run_dir / "metrics.json").exists())
        self.assertTrue((self.run_dir / "pi_session.jsonl").is_file())

    def test_run_task_rc0_but_empty_output_is_recorded(self):
        # The agent settled without delivering: that is its result (judged as such, and the
        # improver sees the trajectory), not an outage to re-roll.
        rc, _ = self._run((stream_lines("DONE"), {}, True))
        self.assertEqual(rc, 0)
        m = json.loads((self.run_dir / "metrics.json").read_text())
        self.assertTrue(m["finished_cleanly"])
        self.assertEqual(m["deliverables"], [])
        self.assertEqual(m["exit_code"], 0)

    def test_run_task_nonzero_exit_with_deliverables_is_recorded(self):
        # A timeout after the memo was written keeps the memo and the trajectory.
        rc, _ = self._run((stream_lines("working", settled=False), {"output/x.docx": "x"}, False, 124))
        self.assertEqual(rc, 0)
        m = json.loads((self.run_dir / "metrics.json").read_text())
        self.assertEqual((m["exit_code"], m["finished_cleanly"], m["deliverables"]), (124, False, ["x.docx"]))
        self.assertIn("wall", (self.run_dir / "pi_stderr.log").read_text())

    def test_run_task_cut_off_without_deliverables_writes_no_metrics(self):
        # Killed before agent_settled with nothing delivered: not a result, so not cached.
        rc, _ = self._run((stream_lines("working", settled=False), {}, False, 137))
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.run_dir / "metrics.json").exists())

    def test_doc_coverage_counts_tool_call_arguments_only(self):
        docs = self.td / "docs"
        docs.mkdir()
        (docs / "a.docx").write_bytes(b"x")
        (docs / "b.pdf").write_bytes(b"x")
        lines = [
            {"type": "message_end", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c0", "name": "bash", "arguments": {"command": "ls documents/"}},
                {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "python3 scripts/read_doc.py documents/a.docx"}},
            ], "usage": {"input": 1, "output": 1}}},
            {"type": "tool_execution_end", "toolCallId": "c0", "toolName": "bash",
             "result": {"content": [{"type": "text", "text": "a.docx\nb.pdf\n"}]}, "isError": False},
            {"type": "agent_settled", "aborted": False},
        ]
        p = self.td / "s.jsonl"
        p.write_text("".join(json.dumps(ln) + "\n" for ln in lines))
        self.assertEqual(L.doc_coverage(L.parse_events(p), docs), (1, 2))

    def test_nudge_loop_stops_on_done(self):
        rc, fake = self._run(
            (stream_lines("working", tool_calls=1), {}, True),
            (stream_lines("DONE", tool_calls=1), {"output/x.docx": "x"}, True),
            (stream_lines("never", tool_calls=1), {}, True),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("--session", fake.calls[1][0])
        self.assertEqual(fake.calls[1][0][-1], L.NUDGE)
        self.assertEqual(json.loads((self.run_dir / "metrics.json").read_text())["continues"], 1)

    def test_nudge_loop_skipped_without_session(self):
        rc, fake = self._run((stream_lines("working"), {}, False))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads((self.run_dir / "metrics.json").read_text())["continues"], 0)

    def test_nudge_loop_bounded(self):
        rc, fake = self._run(
            (stream_lines("working", tool_calls=1), {}, True),
            ("", {}, True),  # resume adds no tool calls -> stop
            (stream_lines("more", tool_calls=1), {}, True),
        )
        self.assertEqual(len(fake.calls), 2)
        self.args.max_continue = 0
        rc, fake = self._run((stream_lines("working", tool_calls=1), {}, True))
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
