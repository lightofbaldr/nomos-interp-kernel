"""PiSubstrate runtime: rendering, invocation, the JSON trajectory renderer, isolation."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config, install
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec
from harness2.core.tree import write_tree
from harness2.substrate import pi
from harness2.substrate.base import Install
from harness2.substrate.pi import (
    PI_SURFACE,
    PiSubstrate,
    compose_append_system,
    final_message,
    harvest_output,
    load_rendered,
    render_events,
    render_pi_workspace,
    stream_usage,
)
from tests.test_pi_surface import TREE

CTX = {"domain_focus": "lawyers", "date": "2026-10-20"}


def _events(directory: Path, *records, torn: str = "") -> Path:
    p = Path(directory) / "events.jsonl"
    text = "".join(json.dumps(r) + "\n" for r in records) + torn
    p.write_text(text, encoding="utf-8")
    return p


def _assistant(*content, usage=None, text=None):
    blocks = list(content)
    if text is not None:
        blocks.append({"type": "text", "text": text})
    msg = {"role": "assistant", "content": blocks, "stopReason": "stop", "timestamp": 2}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "message_end", "message": msg}


USER = {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}
TURN = _assistant(
    {"type": "thinking", "thinking": "let me think"},
    {"type": "text", "text": "I will run two tools"},
    {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "ls"}},
    {"type": "toolCall", "id": "c2", "name": "read", "arguments": {"path": "a.txt"}},
    usage={"input": 100, "output": 5, "reasoning": 2, "cost": {"total": 0.01}},
)
READ_END = {
    "type": "tool_execution_end",
    "toolCallId": "c2",
    "toolName": "read",
    "result": {"content": [{"type": "text", "text": "file text"}]},
    "isError": False,
}
READ_MSG = {
    "type": "message_end",
    "message": {
        "role": "toolResult",
        "toolCallId": "c2",
        "toolName": "read",
        "content": [{"type": "text", "text": "file text"}],
        "isError": False,
    },
}
BASH_END = {
    "type": "tool_execution_end",
    "toolCallId": "c1",
    "toolName": "bash",
    "result": {"content": [{"type": "text", "text": "ls: cannot access"}]},
    "isError": True,
}
RETRY_START = {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000, "errorMessage": "529"}
RETRY_FAIL = {"type": "auto_retry_end", "success": False, "attempt": 3, "finalError": "boom"}
SETTLED = {"type": "agent_settled", "aborted": False}
TURN_END = {"type": "turn_end", "message": {}, "toolResults": []}
UPDATE = {"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "text_delta", "delta": "x"}}


def _spec(td: Path, mode: str) -> BenchSpec:
    ch = ChannelSpec("claude_cli", "opus")
    return BenchSpec(
        name="t",
        runs_root=td,
        install_mode=mode,
        prompts=BenchPrompts(work="w"),
        channels={"proposer": ch, "selector": ch},
    )


class RenderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.tree_dir = write_tree(PI_SURFACE, TREE, self.td / "tree")

    def test_compose_append_system_interpolates_and_strips_comments(self):
        staged = write_tree(
            PI_SURFACE,
            {"systemprompt.md": "Role {{domain_focus}}", "guardrails.md": "<!-- c6 -->"},
            self.td / "s",
        )
        body = compose_append_system(staged, CTX)
        self.assertIn("Role lawyers", body)
        self.assertNotIn("Operating Guardrails", body)
        self.assertNotIn("<!--", body)

    def test_render_pi_workspace_layout(self):
        (self.tree_dir / "scripts" / "_hidden.py").write_text("x", encoding="utf-8")
        rd = self.td / "render"
        rendered = render_pi_workspace(self.tree_dir, rd, CTX)
        self.assertTrue((rd / ".pi" / "skills" / "count_files" / "scripts" / "count.py").is_file())
        # the path the improver prompt promises ("python3 skills/<name>/scripts/<helper>") exists too
        self.assertTrue((rd / "skills" / "count_files" / "scripts" / "count.py").is_file())
        self.assertFalse((rd / "skills" / "count_files" / "SKILL.md").exists())
        self.assertTrue((rd / ".pi" / "extensions" / "guard.ts").is_file())
        self.assertTrue((rd / "scripts" / "check.py").is_file())
        self.assertFalse((rd / "scripts" / "_hidden.py").exists())
        self.assertEqual(rendered.append_system, rd / ".harness2" / "APPEND_SYSTEM.md")
        self.assertTrue(rendered.append_system.is_file())
        self.assertEqual([s.name for s in rendered.skills], ["count_files"])
        self.assertEqual([e.name for e in rendered.extensions], ["guard.ts"])
        self.assertEqual(rendered.scripts, ("check.py",))
        self.assertEqual(load_rendered(rd), rendered)

    def test_render_pi_workspace_empty_always_on_has_no_append_file(self):
        bare = write_tree(PI_SURFACE, {a: "" for a in PI_SURFACE.always_on}, self.td / "bare")
        rendered = render_pi_workspace(bare, self.td / "r2", CTX)
        self.assertIsNone(rendered.append_system)
        self.assertFalse((self.td / "r2" / ".harness2").exists())

    def test_materialise_requires_pi_workspace_mode(self):
        sub = PiSubstrate()
        with self.assertRaises(ValueError):
            sub.materialise(self.tree_dir, _spec(self.td, "codex_root"), {})
        inst = sub.materialise(self.tree_dir, _spec(self.td, "pi_workspace"), CTX)
        self.addCleanup(lambda: __import__("shutil").rmtree(inst.staging_dir, ignore_errors=True))
        self.assertTrue(inst.extras["rendered"].endswith("_rendered"))
        self.assertTrue((Path(inst.extras["rendered"]) / ".pi" / "extensions" / "guard.ts").is_file())
        self.assertEqual(inst.extras["install_mode"], "pi_workspace")


class ArgvEnvTests(unittest.TestCase):
    def _rendered(self, append=True):
        return pi.RenderedHarness(
            root=Path("/r"),
            append_system=Path("/r/.harness2/APPEND_SYSTEM.md") if append else None,
            skills=(Path("/r/.pi/skills/count_files"),),
            extensions=(Path("/r/.pi/extensions/guard.ts"),),
            scripts=("check.py",),
        )

    def test_pi_argv_flags(self):
        argv = PiSubstrate().pi_argv(
            "do it",
            Path("/ws"),
            "zai/glm-5.3-flash",
            thinking="high",
            tools="read,bash",
            session_dir=Path("/s"),
            name="lbl",
            rendered=self._rendered(),
            binary="pi",
        )
        for f in (
            "--mode", "json", "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-mcp", "--no-context-files", "--no-approve", "--offline",
            "--thinking", "--tools", "--session-dir", "--name",
        ):
            self.assertIn(f, argv)
        self.assertEqual(argv[0], "pi")
        self.assertEqual(argv[-2:], ["--", "do it"])
        self.assertEqual(argv[argv.index("--model") + 1], "zai/glm-5.3-flash")
        self.assertEqual(argv[argv.index("--thinking") + 1], "high")
        self.assertEqual(argv[argv.index("--tools") + 1], "read,bash")
        self.assertEqual(argv.count("--skill"), 1)
        self.assertEqual(argv.count("-e"), 1)
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "/r/.harness2/APPEND_SYSTEM.md")
        bare = PiSubstrate().pi_argv(
            "p", Path("/ws"), "m", thinking="low", tools="read", session_dir=Path("/s"),
            name="n", rendered=self._rendered(append=False), binary="pi",
        )
        self.assertNotIn("--append-system-prompt", bare)

    def test_pi_env_isolation(self):
        env = PiSubstrate().pi_env(
            {"PATH": "/x", "PI_CODING_AGENT_SESSION_DIR": "/elsewhere"}, agent_dir=Path("/agent")
        )
        self.assertEqual(env["PI_CODING_AGENT_DIR"], "/agent")
        self.assertNotIn("PI_CODING_AGENT_SESSION_DIR", env)
        self.assertEqual(env["PI_SKIP_VERSION_CHECK"], "1")
        self.assertEqual(env["PI_TELEMETRY"], "0")
        self.assertEqual(env["PATH"], "/x")


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)

    def test_render_events_basic(self):
        p = _events(
            self.td, USER, UPDATE, TURN, TURN_END, READ_END, READ_MSG, BASH_END,
            RETRY_START, RETRY_FAIL, SETTLED,
        )
        lines = render_events(p, obs_trunc=50_000, max_chars=10**6).splitlines()
        self.assertEqual(lines[0], "[USER] hello")
        self.assertEqual(lines[1], "[THINKING] let me think")
        self.assertEqual(lines[2], "[ASSISTANT] I will run two tools")
        self.assertTrue(lines[3].startswith("[ACTION] bash("))
        self.assertTrue(lines[4].startswith("[ACTION] read("))
        self.assertEqual(lines[5], "[OBSERVATION] read: file text")
        self.assertEqual(lines[6], "[OBSERVATION ERROR] bash: ls: cannot access")
        self.assertIn("[ERROR] boom", lines[7])
        self.assertEqual(len(lines), 8)
        self.assertEqual(sum(ln.startswith("[OBSERVATION") for ln in lines), 2)

    def test_render_events_clips_args_and_observations(self):
        big = {
            "type": "tool_execution_end", "toolCallId": "c1", "toolName": "bash",
            "result": {"content": [{"type": "text", "text": "x" * 20_000}]}, "isError": False,
        }
        p = _events(self.td, TURN, big, SETTLED)
        out = render_events(p, obs_trunc=10_000, max_chars=10**6, args_trunc=10)
        action = next(ln for ln in out.splitlines() if ln.startswith("[ACTION] bash("))
        self.assertIn("…(+", action)
        obs = next(ln for ln in out.splitlines() if ln.startswith("[OBSERVATION] bash:"))
        self.assertIn("…(+10000 chars", obs)

    def test_render_events_max_chars(self):
        p = _events(self.td, USER, TURN, SETTLED)
        out = render_events(p, obs_trunc=50_000, max_chars=40)
        self.assertLess(len(out), 120)
        self.assertIn("render truncation", out)

    def test_render_events_torn_stream(self):
        p = _events(self.td, USER, TURN, torn='{"type":"tool_execution_end","toolCallId":"c1","toolName":"bash","result":{"content":[{"type":"te')
        lines = render_events(p, obs_trunc=50_000, max_chars=10**6).splitlines()
        self.assertEqual(lines[-1], "[ERROR] stream ended before agent_settled")
        self.assertEqual(lines[0], "[USER] hello")

    def test_render_events_aborted_and_empty(self):
        p = _events(self.td, USER, {"type": "agent_settled", "aborted": True})
        self.assertEqual(render_events(p, obs_trunc=100, max_chars=1000).splitlines()[-1], "[ERROR] aborted")
        empty = _events(self.td, torn="not json at all\n")
        self.assertEqual(render_events(empty, obs_trunc=100, max_chars=1000), "(no parseable events in trajectory)")

    def test_stream_usage_sums(self):
        second = _assistant(
            text="done", usage={"input": 100, "output": 5, "reasoning": 2, "cost": {"total": 0.01}}
        )
        p = _events(self.td, USER, TURN, TURN_END, READ_END, BASH_END, second, TURN_END, SETTLED)
        u = stream_usage(p)
        self.assertEqual((u["input"], u["output"], u["reasoning"]), (200, 10, 4))
        self.assertAlmostEqual(u["cost_usd"], 0.02)
        self.assertEqual((u["turns"], u["tool_calls"], u["settled"], u["aborted"]), (2, 2, True, False))
        self.assertEqual(final_message(p), "done")


class ExecuteAndPreflightTests(unittest.TestCase):
    def test_execute_respects_semaphore(self):
        lock, state = threading.Lock(), {"cur": 0, "max": 0}

        def launcher(req, inst):
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            time.sleep(0.05)
            with lock:
                state["cur"] -= 1
            return [Path("/tmp")]

        inst = Install(staging_dir=Path("/tmp"), fingerprint="f", extras={})
        sub = PiSubstrate(launcher=launcher)
        with patch.object(pi, "_PI_SEM", threading.BoundedSemaphore(1)):
            threads = [threading.Thread(target=sub.execute, args=("t", inst, ["r"], 10)) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(state["max"], 1)
        with self.assertRaises(RuntimeError):
            PiSubstrate().execute("t", inst, ["r"], 10)

    def _agent_dir(self, td: Path) -> Path:
        src = td / "src"
        src.mkdir()
        (src / "models.json").write_text("{}")
        (src / "auth.json").write_text("{}")
        agent = td / "agent"
        agent.mkdir()
        (agent / "settings.json").write_text("{}")
        (agent / "models.json").symlink_to(src / "models.json")
        (agent / "auth.json").symlink_to(src / "auth.json")
        return agent

    def _fake_run(self, list_models_output):
        def run(argv, **kw):
            if "--version" in argv and argv[0].endswith("node"):
                return SimpleNamespace(stdout="v22.23.3\n", returncode=0, stderr="")
            if "--version" in argv:
                return SimpleNamespace(stdout="1.3.0\n", returncode=0, stderr="")
            if "--list-models" in argv:
                return SimpleNamespace(stdout=list_models_output, returncode=0, stderr="")
            raise AssertionError(argv)

        return run

    def test_preflight_missing_model_names_remedy(self):
        with tempfile.TemporaryDirectory() as td:
            agent = self._agent_dir(Path(td))
            out = "provider   model          context\nzai        glm-5.2        1.0M\n"
            with (
                patch.object(config, "PI_AGENT_DIR", agent),
                patch.object(config, "SOLVER_MODEL_PI", "zai/glm-5.3-flash"),
                patch("harness2.substrate.pi.shutil.which", side_effect=lambda b: f"/bin/{b}"),
                patch("harness2.substrate.pi.subprocess.run", self._fake_run(out)),
            ):
                with self.assertRaisesRegex(RuntimeError, "pi update --models"):
                    PiSubstrate().preflight()
                good = out + "zai        glm-5.3-flash  1M\n"
                with patch("harness2.substrate.pi.subprocess.run", self._fake_run(good)):
                    PiSubstrate().preflight()  # resolves: no raise

    def test_preflight_requires_agent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(config, "PI_AGENT_DIR", Path(td) / "missing"),
                patch("harness2.substrate.pi.shutil.which", side_effect=lambda b: f"/bin/{b}"),
                patch("harness2.substrate.pi.subprocess.run", self._fake_run("")),
            ):
                with self.assertRaisesRegex(RuntimeError, "harness2-setup"):
                    PiSubstrate().preflight()


class AgentDirTests(unittest.TestCase):
    def test_pi_agent_dir_links(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            (src / "models.json").write_text('{"providers": {}}')
            (src / "auth.json").write_text("{}")
            dest = Path(td) / "deps" / "pi" / "agent"
            runs: list[list[str]] = []
            with (
                patch.object(config, "PI_AGENT_DIR", dest),
                patch.object(install, "run", side_effect=lambda cmd, **kw: runs.append([str(c) for c in cmd])),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("HARNESS2_PI_MODELS_JSON", None)
                os.environ.pop("HARNESS2_PI_AUTH_JSON", None)
                got = install.pi_agent_dir(source=src)
                self.assertEqual(got, dest)
                settings = json.loads((dest / "settings.json").read_text())
                self.assertEqual(settings["defaultProjectTrust"], "never")
                self.assertEqual(settings["packages"], [])
                self.assertIn("-builtin:mcp", settings["extensions"])
                self.assertEqual((dest / "models.json").resolve(), (src / "models.json").resolve())
                self.assertEqual((dest / "auth.json").resolve(), (src / "auth.json").resolve())
                self.assertTrue(any("update" in r and "--models" in r for r in runs))
                install.pi_agent_dir(source=src)  # idempotent: links replaced, no error
                (dest / "models.json").unlink()
                (dest / "models.json").write_text("{}")
                with self.assertRaisesRegex(RuntimeError, "regular file"):
                    install.pi_agent_dir(source=src)


if __name__ == "__main__":
    unittest.main()


FIXTURE = Path(__file__).parent / "fixtures" / "pi_events_smoke.jsonl"


class RecordedStreamTests(unittest.TestCase):
    """A real `pi --mode json` stream (Pi 1.3.0, zai/glm-5.3-flash, 2026-10-20), paths scrubbed."""

    def test_render_events_smoke_fixture(self):
        out = render_events(FIXTURE, obs_trunc=2000, max_chars=10**6)
        self.assertIn("[ACTION] read(", out)
        self.assertIn("[ACTION] workspace_note(", out)
        self.assertIn("[OBSERVATION ERROR] bash: rm -rf is not allowed", out)
        self.assertEqual(out.count("[OBSERVATION"), 4)
        self.assertNotIn("stream ended before agent_settled", out)
        usage = stream_usage(FIXTURE)
        self.assertTrue(usage["settled"])
        self.assertEqual(usage["tool_calls"], 4)
        self.assertGreater(usage["input"], 0)
        self.assertIn("workspace_note", final_message(FIXTURE))


class HarvestTests(unittest.TestCase):
    def test_harvest_output_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "ws" / "output"
            (out / "sub").mkdir(parents=True)
            (out / "answer.txt").write_text("42")
            (out / "sub" / "deep.txt").write_text("x")
            secret = td / "secret.json"
            secret.write_text("{}")
            (out / "evil.json").symlink_to(secret)
            (out / "sub" / "evil2").symlink_to(td)
            final = td / "final"
            self.assertEqual(harvest_output(out, final), 2)
            files = sorted(p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file())
            self.assertEqual(files, ["answer.txt", "sub/deep.txt"])
            self.assertFalse((final / "evil.json").exists())
            self.assertFalse((final / "sub" / "evil2").exists())
