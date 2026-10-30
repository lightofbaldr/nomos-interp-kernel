"""Mojo's Pi launcher; never uses the legacy unrestricted launcher or host Pi auth."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path

from ..core import tree as HT
from ..substrate.model_broker import MODEL, ModelBroker
from ..substrate.pi import (
    PI_SURFACE,
    PiSubstrate,
    RenderedHarness,
    parse_events,
    render_pi_workspace,
    stream_usage,
)
from ..substrate.sandbox import Sandbox
from .mojo_tasks import Task


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def file_bytes(path: Path, *, limit=1_048_576) -> bytes:
    """Harvest only regular single-link files after all sandbox processes have exited."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("invalid or oversized artifact")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise ValueError("oversized artifact")
        return data
    finally:
        os.close(fd)


def load_completed(run_dir: Path):
    saved = json.loads(file_bytes(run_dir / "complete.json"))
    expected = {"pi.jsonl", "stderr.txt", "result.json"}
    if (run_dir / "solution.mojo").exists():
        expected.add("solution.mojo")
    if set(saved.get("outputs", {})) != expected:
        raise ValueError("completed rollout does not bind the exact output set")
    for name, digest in saved["outputs"].items():
        if hashlib.sha256(file_bytes(run_dir / name, limit=16 * 1024 * 1024)).hexdigest() != digest:
            raise ValueError("rollout output changed")
    return saved


def harness_tree(path: Path):
    for p in path.rglob("*"):
        if p.is_symlink() or (not p.is_file() and not p.is_dir()):
            raise ValueError("harness contains a link or special file")
    tree = HT.read_tree(PI_SURFACE, path)
    HT.validate(PI_SURFACE, tree, allow_subagents=False)
    if sum(map(len, tree.values())) > 80_000:
        raise ValueError("harness exceeds the compact Mojo surface budget")
    if sum(len(tree.get(k, "")) for k in PI_SURFACE.always_on) > 6000:
        raise ValueError("Mojo always-on text exceeds 6000 characters")
    return tree


def prepare_agent(work: Path):
    agent = work / ".agent"
    agent.mkdir()
    write_json(
        agent / "settings.json",
        {
            "defaultProjectTrust": "never",
            "quietStartup": True,
            "packages": [],
            "extensions": [],
            "retry": {"enabled": False, "provider": {"maxRetries": 0}},
            "compaction": {"enabled": False},
            "defaultThinkingLevel": "high",
        },
    )
    write_json(agent / "auth.json", {})
    write_json(
        agent / "models.json",
        {
            "providers": {
                "nomos": {
                    "baseUrl": "http://127.0.0.1:8091/v1",
                    "api": "openai-completions",
                    "apiKey": "local-no-secret",
                    "models": [
                        {
                            "id": MODEL,
                            "name": "Glimmer",
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 32768,
                            "maxTokens": 8192,
                            "samplingParams": {"temperature": 0},
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        }
                    ],
                }
            }
        },
    )


class Runner:
    def __init__(self, sandbox: Sandbox, *, approved=False, _broker=ModelBroker):
        self.sandbox, self.approved, self.broker = sandbox, approved, _broker

    def run(self, task: Task, harness: Path, run_dir: Path, *, timeout=600):
        if not self.approved:
            raise RuntimeError("live Pi rollouts are held; obtain Adam's approval through Prime")
        tree = harness_tree(harness)
        identity = {
            "task": task.sha256,
            "harness": hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest(),
            "model": MODEL,
            "temperature": 0,
            "thinking": "high",
            "sandbox": "bwrap-model-only-v1",
            "runtime": self.sandbox.identity(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        if run_dir.exists():
            done = run_dir / "complete.json"
            if not done.is_file():
                raise ValueError("partial rollout exists; preserve it and choose a fresh run id")
            saved = load_completed(run_dir)
            if saved.get("identity") != identity:
                raise ValueError("rollout identity changed")
            return run_dir
        run_dir.mkdir(parents=True)
        work = run_dir / "workspace"
        render_pi_workspace(
            harness,
            work,
            {
                "working_directory": "/work",
                "domain_focus": "Mojo 1.0",
                "date": "2026-10-30",
                "env_description": "Isolated CPU workspace; no web access.",
            },
        )
        prepare_agent(work)
        inside = Path("/work")
        rendered = RenderedHarness(
            inside,
            inside / ".harness2/APPEND_SYSTEM.md"
            if (work / ".harness2/APPEND_SYSTEM.md").is_file()
            else None,
            tuple(
                inside / p.parent.relative_to(work)
                for p in sorted(work.glob(".pi/skills/*/SKILL.md"))
            ),
            tuple(inside / p.relative_to(work) for p in sorted(work.glob(".pi/extensions/*.ts"))),
            (),
        )
        args = PiSubstrate().pi_argv(
            task.instructions,
            inside,
            "nomos/" + MODEL,
            thinking="high",
            tools="+grep,+find,+ls",
            name=task.id,
            rendered=rendered,
            binary="/opt/pi/dist/bundle/cli.js",
        )
        # Use the pinned Node binary, not an inherited user's launcher or config.
        args = ["/opt/node/node", *args]
        write_json(
            run_dir / "launch.json",
            {"identity": identity, "argv": args, "stdin": "DEVNULL", "max_model_calls": 32},
        )
        with tempfile.TemporaryDirectory(prefix="h2-model-") as private:
            socket = Path(private) / "model.sock"
            with self.broker(socket, max_calls=32) as broker:
                result = self.sandbox.run(
                    work, args, timeout=timeout, broker=socket, output_limit=16 * 1024 * 1024
                )
                calls = broker.calls
        (run_dir / "pi.jsonl").write_text(result.stdout)
        (run_dir / "stderr.txt").write_text(result.stderr)
        usage = stream_usage(run_dir / "pi.jsonl")
        if self.sandbox.identity() != identity["runtime"]:
            raise RuntimeError("runtime changed during rollout; not completed")
        assistants = [
            e.get("message", {})
            for e in parse_events(run_dir / "pi.jsonl")
            if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
        ]
        terminal_ok = bool(assistants) and assistants[-1].get("stopReason") in ("stop", "length")
        if (
            result.reason
            or result.returncode != 0
            or not usage["settled"]
            or usage["aborted"]
            or not terminal_ok
        ):
            write_json(
                run_dir / "failed.json",
                {"identity": identity, "process": asdict(result), "model_calls": calls},
            )
            raise RuntimeError("Pi rollout failed or did not settle; not scored")
        source = work / "solution.mojo"
        outputs = ["pi.jsonl", "stderr.txt", "result.json"]
        try:
            code = file_bytes(source)
            code.decode("utf-8")
        except FileNotFoundError:
            code = None  # settled, no deliverable is an observed failure, not an outage
        except (OSError, ValueError, UnicodeError):
            raise RuntimeError("unsafe or invalid Pi output; not scored") from None
        if code is not None:
            (run_dir / "solution.mojo").write_bytes(code)
            outputs.append("solution.mojo")
        write_json(
            run_dir / "result.json",
            {
                "usage": usage,
                "model_calls": calls,
                "seconds": result.seconds,
                "delivered": bool(code),
            },
        )
        write_json(
            run_dir / "complete.json",
            {
                "identity": identity,
                "outputs": {
                    name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                    for name in outputs
                },
            },
        )
        return run_dir
