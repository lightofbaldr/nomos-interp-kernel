"""JobBench on the Pi substrate: spec, adapter wiring, base harness, and the Pi runner."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import jb_pi_runner as R
from harness2.benchmarks.jobbench import PI_RUNNER, JobBenchAdapter, make_spec
from harness2.core.tree import read_tree, write_tree
from harness2.substrate.base import Install, RunRequest
from harness2.substrate.pi import PI_SURFACE, PiSubstrate, render_pi_workspace
from tests.test_pi_surface import TREE


class AdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)

    def test_make_spec_pi(self):
        s = make_spec("pi")
        self.assertEqual((s.install_mode, s.allow_subagents, s.name), ("pi_workspace", False, "jb"))
        with self.assertRaises(ValueError):
            make_spec("nope")

    def test_adapter_uses_pi_substrate(self):
        a = JobBenchAdapter(substrate="pi")
        self.assertIsInstance(a.substrate, PiSubstrate)
        self.assertEqual(a.timeout, 2400)
        self.assertEqual(a.substrate.name, "pi")

    def test_base_harness_pi_is_bare(self):
        with patch.object(config, "RUNS", self.td):
            a = JobBenchAdapter(substrate="pi")
            base = a.base_harness()
        self.assertEqual(read_tree(PI_SURFACE, base), {k: "" for k in PI_SURFACE.always_on})
        self.assertTrue(str(base).startswith(str(self.td)))

    def test_launch_pi_env_contract(self):
        a = JobBenchAdapter(substrate="pi", store_root=self.td / "store")
        install = Install(
            staging_dir=self.td, fingerprint="f",
            extras={"install_mode": "pi_workspace", "rendered": str(self.td / "_rendered")},
        )
        req = RunRequest(task_ref="lawyers/task1", run_ids=("jb-pi-agg1/lawyers/task1/base1",), timeout=100)
        with (
            patch.object(a, "_run_runner") as runner,
            patch.object(a, "_require_artifacts", return_value=self.td / "out") as req_art,
            patch.object(a.substrate, "preflight"),
        ):
            dirs = a._launch_pi(req, install)
        self.assertEqual(dirs, [self.td / "out"])
        (runner_path, env), kw = runner.call_args.args, runner.call_args.kwargs
        self.assertEqual(runner_path, PI_RUNNER)
        self.assertEqual(kw["wall"], 800)
        for k in (
            "TASK_IDS", "LABEL", "HARNESS_RENDER_DIR", "TASKS_BASE_DIR", "MODEL_ID", "PI_BIN",
            "PI_AGENT_DIR", "PI_THINKING", "PI_TOOLS", "TIMEOUT_PER_TASK", "MAX_RETRIES",
            "PI_CODING_AGENT_DIR",
        ):
            self.assertIn(k, env, k)
        self.assertEqual(env["HARNESS_RENDER_DIR"], str(self.td / "_rendered"))
        self.assertEqual(env["LABEL"], "jb-pi-agg1__lawyers__task1__base1")
        self.assertEqual(env["TASKS_BASE_DIR"], str(self.td / "store"))
        self.assertEqual(env["MAX_RETRIES"], "2")
        req_art.assert_called_once_with("lawyers/task1", "jb-pi-agg1__lawyers__task1__base1")

    def test_run_judge_uses_configured_workers(self):
        deps = self.td / "deps"
        repo = deps / "job-bench-eval"
        (repo / "eval").mkdir(parents=True)
        (repo / "eval" / "judge.py").write_text("")
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "python").write_text("")
        task = repo / "dataset" / "main" / "lawyers" / "task1"
        task.mkdir(parents=True)
        (task / "RUBRICS.json").write_text('{"rubrics": []}')
        calls = []

        def fake(argv, **kw):
            calls.append((list(argv), kw))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(config, "DEPENDENCIES", deps),
            patch.object(config, "JB_JUDGE_PYTHON", repo / ".venv" / "bin" / "python"),
            patch.object(config, "JB_JUDGE_API_KEY", "k"),
            patch.object(config, "JB_JUDGE_API_BASE", "http://127.0.0.1:1/v1"),
            patch.object(config, "JUDGE_WORKERS", 4),
            patch("harness2.benchmarks.jobbench.subprocess.run", fake),
        ):
            a = JobBenchAdapter(substrate="pi", store_root=self.td / "store")
            ok = a._run_judge("lawyers/task1", "lbl", self.td / "run")
        self.assertTrue(ok)
        argv, kw = calls[0]
        self.assertEqual(argv[argv.index("--max-workers") + 1], "4")
        self.assertEqual(kw["env"]["JUDGE_REASONING_EFFORT"], config.JUDGE_EFFORT)
        self.assertEqual(kw["env"]["JUDGE_API_KEY"], "k")
        # Judge calls through the CLI bridge can be slow (rate limits); both the per-rubric
        # client timeout and the judge subprocess wall are settings, not upstream's constants.
        self.assertEqual(argv[argv.index("--timeout-per-rubric") + 1], str(config.JUDGE_RUBRIC_TIMEOUT))
        self.assertEqual(kw["timeout"], config.JUDGE_TIMEOUT)
        self.assertGreaterEqual(config.JUDGE_RUBRIC_TIMEOUT, 600)
        self.assertGreaterEqual(config.JUDGE_TIMEOUT, 3600)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        tree = write_tree(PI_SURFACE, TREE, self.td / "tree")
        self.render_dir = self.td / "render"
        render_pi_workspace(tree, self.render_dir, {})
        self.task_dir = self.td / "tasks" / "lawyers" / "task1" / "task_folder"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "TASK_INSTRUCTIONS.txt").write_text("do the thing")
        self.cfg = R.Settings(
            task_ids=("lawyers/task1",), label="lbl", render_dir=self.render_dir,
            tasks_base=self.td / "tasks", pi_bin="pi", agent_dir=self.td / "agent",
            model_id="zai/glm-5.3-flash", thinking="high", tools="+grep,+find,+ls",
            timeout_per_task="2400", max_retries=1, log_dir=self.td / "logs",
        )
        base = patch.object(R, "TEMP_BASE", self.td / "hevo")
        base.start()
        self.addCleanup(base.stop)

    def test_runner_stage_layout(self):
        ws = self.td / "ws"
        rendered = R._stage(self.task_dir, self.render_dir, ws)
        for rel in (
            "task_folder/TASK_INSTRUCTIONS.txt", "scripts/check.py",
            ".pi/skills/count_files/SKILL.md", ".pi/extensions/guard.ts", ".harness2/APPEND_SYSTEM.md",
            "skills/count_files/scripts/count.py",
        ):
            self.assertTrue((ws / rel).is_file(), rel)
        self.assertTrue((ws / "output").is_dir())
        self.assertEqual([s.name for s in rendered.skills], ["count_files"])
        self.assertEqual(rendered.root, ws)

    def test_runner_command(self):
        ws = self.td / "ws"
        rendered = R._stage(self.task_dir, self.render_dir, ws)
        prompt = R.PROMPT.format(task_folder=ws / "task_folder", output_dir=ws / "output", workspace=ws)
        argv = R._command(self.cfg, ws, prompt, rendered, ws / "s", "n")
        self.assertEqual(argv[:3], ["timeout", "--kill-after=30s", "2400"])
        self.assertEqual(argv[3], "pi")
        self.assertIn("--mode", argv)
        self.assertTrue(argv[-1].startswith("=== TASK FOLDER ==="))
        self.assertEqual(argv[argv.index("--model") + 1], "zai/glm-5.3-flash")

    def test_attempt_keeps_only_json_lines(self):
        ws = self.td / "ws"
        rendered = R._stage(self.task_dir, self.render_dir, ws)
        traj = self.td / "traj" / "t.jsonl"
        traj.parent.mkdir()
        seen = {}

        def fake(argv, **kw):
            kw["stdout"].write('banner\n{"type":"agent_start"}\n')
            kw["stderr"].write("warn\n")
            seen.update(kw)
            return SimpleNamespace(returncode=0)

        with patch.object(R.subprocess, "run", fake):
            code = R._attempt(self.cfg, ws, traj, self.td / "run.log", "lawyers_task1", 1, rendered)
        self.assertEqual(code, 0)
        self.assertEqual(traj.read_text(), '{"type":"agent_start"}\n')
        self.assertEqual(seen["env"]["PI_CODING_AGENT_DIR"], str(self.td / "agent"))
        self.assertEqual(seen["cwd"], ws)
        self.assertIn("warn", (self.td / "run.log").read_text())
        self.assertEqual([p.name for p in traj.parent.iterdir()], ["t.jsonl"])  # no stdout tempfile left

    def test_run_one_without_output_is_failure(self):
        def fake(argv, **kw):
            kw["stdout"].write('{"type":"agent_settled","aborted":false}\n')
            return SimpleNamespace(returncode=0)

        with patch.object(R.subprocess, "run", fake):
            ok = R.run_one(self.cfg, "lawyers/task1")
        self.assertFalse(ok)
        self.assertFalse((self.td / "tasks" / "lawyers" / "task1" / "model_output" / "lbl").exists())
        traj_dir = self.td / "tasks" / "lawyers" / "task1" / "model_traj" / "lbl"
        self.assertEqual(len(list(traj_dir.glob("*.jsonl"))), 1)

    def test_run_one_moves_output(self):
        def fake(argv, **kw):
            (Path(kw["cwd"]) / "output" / "answer.txt").write_text("42")
            kw["stdout"].write('{"type":"agent_settled","aborted":false}\n')
            return SimpleNamespace(returncode=0)

        with patch.object(R.subprocess, "run", fake):
            ok = R.run_one(self.cfg, "lawyers/task1")
        self.assertTrue(ok)
        out = self.td / "tasks" / "lawyers" / "task1" / "model_output" / "lbl" / "answer.txt"
        self.assertEqual(out.read_text(), "42")
        self.assertFalse((self.td / "hevo" / "lawyers_task1_lbl_" ).exists())

    def test_settings_from_env(self):
        env = {
            "TASK_IDS": "a/b c/d", "LABEL": "l", "HARNESS_RENDER_DIR": str(self.render_dir),
            "TASKS_BASE_DIR": str(self.td), "MODEL_ID": "zai/x", "PI_BIN": "pi",
            "PI_AGENT_DIR": str(self.td), "PI_THINKING": "low", "PI_TOOLS": "+grep",
            "TIMEOUT_PER_TASK": "10", "MAX_RETRIES": "3",
        }
        with patch.dict(R.os.environ, env, clear=False):
            cfg = R._settings()
        self.assertEqual((cfg.task_ids, cfg.max_retries, cfg.thinking), (("a/b", "c/d"), 3, "low"))
        with patch.dict(R.os.environ, {**env, "HARNESS_RENDER_DIR": str(self.td / "missing")}):
            with self.assertRaises(SystemExit):
                R._settings()


if __name__ == "__main__":
    unittest.main()


class RunnerHardeningTests(unittest.TestCase):
    """Security follow-ups: a minimal child environment, a runtime path guard, no symlinks out."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.td = Path(tmp.name)
        self.cfg = R.Settings(
            task_ids=("lawyers/task1",), label="lbl", render_dir=self.td, tasks_base=self.td,
            pi_bin="pi", agent_dir=self.td / "agent", model_id="zai/x", thinking="low",
            tools="+grep", timeout_per_task="10", max_retries=1, log_dir=self.td / "logs",
        )

    def test_env_allowlist_drops_secrets(self):
        with patch.dict(R.os.environ, {
            "ANTHROPIC_API_KEY": "sk", "HARNESS2_JUDGE_API_KEY": "k", "ZAI_API_KEY": "z",
            "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8", "AWS_SECRET_ACCESS_KEY": "s",
            "HARNESS2_PI_PASS_ENV": "ZAI_API_KEY",
        }):
            env = R._env(self.cfg, workspace=self.td / "ws")
        for k in ("ANTHROPIC_API_KEY", "HARNESS2_JUDGE_API_KEY", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(k, env)
        for k in ("PATH", "HOME", "LANG", "PI_CODING_AGENT_DIR", "ZAI_API_KEY"):
            self.assertIn(k, env)
        self.assertEqual(env["HARNESS2_WORKSPACE_ROOT"], str(self.td / "ws"))

    def test_command_loads_runtime_guard(self):
        rendered = pi_render = render_pi_workspace(
            write_tree(PI_SURFACE, TREE, self.td / "tree"), self.td / "r", {}
        )
        argv = R._command(self.cfg, self.td / "ws", "p", rendered, self.td / "s", "n")
        guard = str(R.GUARD_EXTENSION)
        self.assertTrue(guard.endswith("pi_guard.ts"))
        self.assertTrue(Path(guard).is_file())
        self.assertEqual(argv.count("-e"), 2)  # the runtime guard plus the harness extension
        self.assertEqual(argv[argv.index("-e") + 1], guard)  # loaded first
        del pi_render

    def test_harvest_skips_symlinks(self):
        out = self.td / "ws" / "output"
        (out / "sub").mkdir(parents=True)
        (out / "answer.txt").write_text("42")
        (out / "sub" / "deep.txt").write_text("x")
        secret = self.td / "secret.json"
        secret.write_text("{}")
        (out / "evil.json").symlink_to(secret)
        (out / "sub" / "evil2").symlink_to(self.td)
        final = self.td / "final"
        moved = R._harvest(out, final)
        self.assertEqual(sorted(p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file()), ["answer.txt", "sub/deep.txt"])
        self.assertFalse((final / "evil.json").exists())
        self.assertFalse((final / "sub" / "evil2").exists())
        self.assertEqual(moved, 2)
