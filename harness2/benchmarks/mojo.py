"""Mojo adapter for the unchanged Harness² recursion on the Pi substrate."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .. import config
from ..core import tree as HT
from ..core.muse_judge import MuseJudge
from ..core.spec import BenchPrompts, BenchSpec, ChannelSpec
from ..substrate.pi import PI_SURFACE, PiSubstrate, render_events
from ..substrate.sandbox import Sandbox
from .mojo_grade import compiler_health, grade
from .mojo_limits import EXECUTOR_CONSTRAINTS
from .mojo_pi_runner import (
    Runner,
    failed_score,
    file_bytes,
    load_completed,
    load_failed,
    write_json,
)
from .mojo_tasks import Corpus

BASE = {name: "" for name in PI_SURFACE.always_on} | {
    "systemprompt.md": "Solve the user's Mojo task in /work/solution.mojo. Read the mojo-core skill "
    "when writing Mojo. Check your work with the local compiler. "
    "Only /work and its private /tmp are writable. No internet. "
    "Do not claim a check passed unless you ran it.\n",
    "skills/mojo-core/SKILL.md": "---\nname: mojo-core\ndescription: Compact Mojo 1.0 syntax reminders\n---\n"
    "Use def, var and comptime; add raises explicitly. Imports are std.*. "
    "Use mut for mutable arguments, out self in constructors, copy() or ^ for ownership. "
    "List[Int], Int, String and print are in the prelude. Define main for complete programs, "
    "but not for function-only tasks. Compiler: mojo build solution.mojo -o program. "
    "Do not import Python or use external libraries unless the task needs them.\n",
}


class MojoAdapter:
    def __init__(
        self,
        *,
        substrate="pi",
        split_file=None,
        corpus=None,
        runs_root=None,
        runner=None,
        sandbox=None,
    ):
        if substrate != "pi":
            raise ValueError("Mojo supports only --substrate pi")
        root = Path(os.environ.get("HARNESS2_MOJO_DATA", Path.home() / "Projects/nomos-mojo-eval"))
        self.corpus = corpus or Corpus(
            root / "tasks_hard.jsonl",
            root / "heldout_eval.jsonl",
            split_file,
            smoke=root / "tasks.jsonl",
            seed=int(os.environ.get("HARNESS2_MOJO_SEED", "20261030")),
            counts={
                s: int(os.environ.get("HARNESS2_MOJO_" + s.upper() + "_COUNT", str(n)))
                for s, n in (("train", 4), ("test", 10), ("smoke", 3))
            },
        )
        auth_mode = os.environ.get("HARNESS2_MOJO_IMPROVER_AUTH", "off")
        if auth_mode not in ("off", "api-key"):
            raise ValueError("HARNESS2_MOJO_IMPROVER_AUTH must be off or api-key")
        mode = os.environ.get("HARNESS2_IMPROVER_MODE", "off")
        if mode not in ("off", "login-tools"):
            raise ValueError("HARNESS2_IMPROVER_MODE must be off or login-tools")
        if mode == "login-tools" and auth_mode != "off":
            raise ValueError("login-tools and API-key sandbox selectors are mutually exclusive")
        channel = ChannelSpec(
            os.environ.get("HARNESS2_IMPROVER_CHANNEL", "claude_cli"),
            config.IMPROVER_MODEL,
            sandbox=(
                "login-tools" if mode == "login-tools" else
                "host" if auth_mode == "off" else "mojo-claude"
            ),
            auth_mode=auth_mode,
        )
        self.spec = BenchSpec(
            "mojo",
            Path(runs_root or config.RUNS / "mojo"),
            "pi_workspace",
            BenchPrompts(
                work="write correct, clear Mojo 1.0 programs",
                deliverable_word="program",
                yardstick="satisfy the stated contract with simple, idiomatic code",
                executor_constraints=EXECUTOR_CONSTRAINTS,
            ),
            {"proposer": channel, "selector": channel},
            leak_globs=("*grade*", "*judge*", "*reference*", "*heldout*"),
        )
        self.substrate = PiSubstrate()
        self._sandbox = sandbox
        self._runner = runner

    @property
    def identity(self):
        return {
            "corpus": self.corpus.identity,
            "executor": "nomos/nomos-muse-glimmer-30b",
            "judge": "muse-wonder-1.0 (optional)",
            "sandbox": "bwrap-model-only-v1",
            "improver_sandbox": (
                "host-cc-login-tools-v1"
                if self.spec.channels["proposer"].sandbox == "login-tools" else
                "trusted-host"
                if self.spec.channels["proposer"].auth_mode == "off"
                else "mojo-claude-api-key-v1"
            ),
            "improver_auth": self.spec.channels["proposer"].auth_mode,
        }

    def runtime(self):
        if self._sandbox is None:
            binary = Path(shutil.which("pi") or "/missing/pi").resolve()
            # Pi's installed bundle entry is <package>/dist/bundle/cli.js.
            pi = Path(os.environ.get("HARNESS2_MOJO_PI_PACKAGE", binary.parents[2]))
            node = Path(shutil.which("node") or "/missing/node")
            mojo = Path(os.environ.get("HARNESS2_MOJO_PREFIX", Path.home() / ".pixi/envs/mojo"))
            self._sandbox = Sandbox(mojo=mojo, node=node, pi=pi)
        return self._sandbox

    def tasks(self, split="all"):
        return self.corpus.ids(split)

    def domain_of(self, task_id):
        return self.corpus.tasks[task_id].domain

    def task_prompt(self, task_id):
        return self.corpus.tasks[task_id].instructions

    def task_files(self, task_id):
        return None

    def base_harness(self):
        root = self.spec.runs_root / "base"
        if not root.exists():
            HT.write_tree(PI_SURFACE, BASE, root, stubs=False)
        if HT.read_tree(PI_SURFACE, root) != BASE:
            raise ValueError("base harness changed; use a fresh runs directory")
        return root

    def validate_tree(self, tree):
        HT.validate(PI_SURFACE, tree, allow_subagents=False)
        if (
            sum(len(tree.get(k, "")) for k in PI_SURFACE.always_on) > 6000
            or sum(map(len, tree.values())) > 80000
        ):
            raise ValueError("keep the Mojo harness compact: always-on <=6000, total <=80000 chars")

    def preflight(self, *, improver=True):
        if improver and not config.IMPROVER_MODEL:
            raise ValueError("set HARNESS2_IMPROVER_MODEL explicitly")
        sandbox = self.runtime()
        if not (sandbox.pi / "dist/bundle/cli.js").is_file():
            raise ValueError("Pi bundle not found")
        package = json.loads((sandbox.pi / "package.json").read_text())
        if package.get("version") != "1.3.0":
            raise ValueError("Mojo runner is pinned to Pi 1.3.0")
        with tempfile.TemporaryDirectory(prefix="h2-preflight-") as tmp:
            for args in (
                ["/usr/bin/true"],
                ["/opt/mojo/bin/mojo", "--version"],
                ["/opt/node/node", "--version"],
            ):
                result = sandbox.run(Path(tmp), list(args), timeout=30)
                if result.returncode or result.reason:
                    raise RuntimeError("sandbox/toolchain preflight failed; no rollout permitted")
                if "mojo" in args[0] and "Mojo 1." not in result.stdout:
                    raise RuntimeError("Mojo 1.x required")
        if not compiler_health(sandbox)["pass"]:
            raise RuntimeError("known-good compiler/run control failed; no rollout permitted")

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        # Harness² names runs as namespace/task/label. Preserve that namespace,
        # but never accept absolute paths, empty segments or traversal.
        if (
            not isinstance(run_id, str)
            or len(run_id) > 720
            or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,239}", part)
                for part in run_id.split("/")
            )
        ):
            raise ValueError("unsafe run id")
        runner = self._runner or Runner(
            self.runtime(), approved=os.environ.get("HARNESS2_MOJO_LIVE_APPROVED") == "1"
        )
        # One local model rollout at a time, including separate Harness² workers.
        self.spec.runs_root.mkdir(parents=True, exist_ok=True)
        with (self.spec.runs_root / ".rollout.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return runner.run(
                self.corpus.tasks[task_id],
                harness_dir or self.base_harness(),
                self.spec.runs_root / "executions" / run_id,
                timeout=timeout or 600,
            )

    def delivered(self, run_dir):
        if not (run_dir / "complete.json").is_file():
            return False
        return "solution.mojo" in load_completed(run_dir)["outputs"]

    def artifacts_intact(self, run_dir):
        try:
            load_completed(run_dir)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def trajectory(self, run_dir, *, obs_trunc):
        prefix = ""
        if (run_dir / "failed.json").is_file():
            saved = load_failed(run_dir)
            prefix = f"[ERROR] Failed rollout: {saved['reason']}; admitted model calls: {saved['model_calls']}\n"
        return prefix + render_events(run_dir / "pi.jsonl", obs_trunc=obs_trunc, max_chars=100_000)

    def deliverable_text(self, run_dir):
        if (run_dir / "failed.json").is_file():
            load_failed(run_dir)
            return ""  # A failed attempt's workspace is not a harvested deliverable.
        try:
            return file_bytes(run_dir / "solution.mojo").decode()
        except FileNotFoundError:
            return ""

    def deliverable_files(self, run_dir):
        return {"solution.mojo": self.deliverable_text(run_dir)} if self.delivered(run_dir) else {}

    def score(self, run_dir, task_id):
        task = self.corpus.tasks[task_id]
        if (run_dir / "failed.json").is_file():
            saved = load_failed(run_dir)
            if saved["identity"]["task"] != task.sha256:
                raise ValueError("wrong task for failed rollout")
            return failed_score(saved)  # No compiler control or Muse request on this path.
        code = self.deliverable_text(run_dir)
        done = load_completed(run_dir)
        if done["identity"]["task"] != task.sha256:
            raise ValueError("wrong task for completed rollout")
        if code and hashlib.sha256(code.encode()).hexdigest() != done["outputs"].get(
            "solution.mojo"
        ):
            raise ValueError("program changed after rollout")
        deterministic = grade(task, code, self.runtime())
        if deterministic["status"] == "unscored":
            write_json(
                run_dir / "mojo-grade.json",
                {
                    "n_passed": None,
                    "n_criteria": 1,
                    "all_pass": None,
                    "ground_truth": deterministic,
                    "muse_quality": {"status": "not_requested", "scores": None},
                    "quality_not_in_ground_truth": True,
                },
            )
            return None
        judge = MuseJudge(
            self.spec.runs_root / "muse-calls.sqlite",
            max_calls=int(os.environ.get("HARNESS2_MUSE_MAX_CALLS", "0")),
            approved=os.environ.get("HARNESS2_MUSE_APPROVED") == "1",
        )
        quality = judge.review(task, code, deterministic)
        combined = {
            "n_passed": int(deterministic["passed"]),
            "n_criteria": 1,
            "all_pass": deterministic["passed"],
            "ground_truth": deterministic,
            "muse_quality": quality,
            "quality_not_in_ground_truth": True,
        }
        write_json(run_dir / "mojo-grade.json", combined)
        return combined
