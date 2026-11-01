"""Mojo's Pi launcher; never uses the legacy unrestricted launcher or host Pi auth."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path

from ..core import tree as HT
from ..core.spec import RecordedRolloutFailure
from ..substrate.model_broker import MODEL, ModelBroker, validate_steer
from ..substrate.pi import (
    PI_SURFACE,
    PiSubstrate,
    RenderedHarness,
    parse_events,
    render_pi_workspace,
    stream_usage,
)
from ..substrate.sandbox import Sandbox
from .mojo_limits import CONTEXT_WINDOW, MODEL_CALL_LIMIT, OUTPUT_TOKENS
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
    if (run_dir / "failed.json").exists():
        raise ValueError("rollout has both complete and failed records")
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


def failure_reason(process, calls, limit, usage, assistants, broker_error=None):
    """Explain an observed unsuccessful terminal path, not a scientific grade."""
    if process.get("reason"):
        return str(process["reason"])[:512]
    if process["returncode"] != 0:
        return f"rollout process exited {process['returncode']}"
    if usage["aborted"]:
        return "Pi run aborted"
    last = assistants[-1] if assistants else {}
    if last.get("stopReason") == "error":
        if broker_error:
            if broker_error["code"] == "model_call_cap" and calls == limit:
                return broker_error["message"]
            return "terminal model error; last broker error: " + broker_error["message"]
        error = str(last.get("errorMessage", ""))
        if calls == limit and error.startswith("429 "):
            return f"call cap reached at {limit}"
        return "terminal model error: " + (error[:480] or "no detail recorded")
    if not usage["settled"]:
        return "Pi stream ended before agent_settled"
    if not assistants or last.get("stopReason") not in ("stop", "length"):
        return "Pi has no successful terminal assistant message"
    raise ValueError("failed record contradicts the successful terminal process/stream")


def assistant_messages(run_dir):
    return [
        e.get("message", {})
        for e in parse_events(run_dir / "pi.jsonl")
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]


def load_failed(run_dir: Path):
    """Verify v2 bindings, or the legacy record's duplicated process bytes and launch.

    Legacy records are read-only: no retroactive binding or rerun is performed.
    """
    if (run_dir / "complete.json").exists():
        raise ValueError("rollout has both complete and failed records")
    saved = json.loads(file_bytes(run_dir / "failed.json", limit=64 * 1024 * 1024))
    launch = json.loads(file_bytes(run_dir / "launch.json"))
    identity, process, calls = saved.get("identity"), saved.get("process"), saved.get("model_calls")
    limit = launch.get("max_model_calls")
    if (
        not isinstance(identity, dict)
        or identity != launch.get("identity")
        or any(
            not isinstance(identity.get(key), str)
            or re.fullmatch(r"[a-f0-9]{64}", identity[key]) is None
            for key in ("task", "harness")
        )
        or identity.get("model") != MODEL
        or not isinstance(process, dict)
        or type(process.get("returncode")) is not int
        or type(limit) is not int
        or not 1 <= limit <= 128
        or type(calls) is not int
        or not 0 <= calls <= limit
    ):
        raise ValueError("invalid failed rollout identity/process/call count")
    names = {"launch.json", "pi.jsonl", "stderr.txt"}
    contents = {name: file_bytes(run_dir / name, limit=16 * 1024 * 1024) for name in names}
    broker_error = saved.get("broker_error")
    if saved.get("v") == 2:
        outputs = saved.get("outputs")
        if not isinstance(outputs, dict) or set(outputs) != names:
            raise ValueError("failed rollout does not bind the exact output set")
        if any(hashlib.sha256(contents[name]).hexdigest() != outputs[name] for name in names):
            raise ValueError("failed rollout output changed")
        if broker_error is not None:
            from ..substrate.model_broker import error_detail

            if not isinstance(broker_error, dict) or broker_error != error_detail(
                broker_error.get("status"), broker_error.get("code"), limit
            ):
                raise ValueError("invalid broker failure diagnostic")
    elif "v" not in saved and "outputs" not in saved and broker_error is None:
        for name, field in (("pi.jsonl", "stdout"), ("stderr.txt", "stderr")):
            if not isinstance(process.get(field), str) or process[field].encode() != contents[name]:
                raise ValueError("legacy failed rollout process bytes disagree")
    else:
        raise ValueError("unsupported failed rollout record")
    reason = failure_reason(
        process,
        calls,
        limit,
        stream_usage(run_dir / "pi.jsonl"),
        assistant_messages(run_dir),
        broker_error,
    )
    if saved.get("v") == 2 and saved.get("reason") != reason:
        raise ValueError("failed rollout reason contradicts its evidence")
    return saved | {"reason": reason}


def failed_score(saved):
    return {
        "n_passed": 0,
        "n_criteria": 1,
        "all_pass": False,
        "ground_truth": {
            "status": "rollout_failed",
            "passed": False,
            "task_sha256": saved["identity"]["task"],
            "reason": saved["reason"],
        },
        "muse_quality": None,
        "quality_not_in_ground_truth": True,
    }


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


# Glimmer's sampling for a rollout. "greedy" is runs 1-2's temperature 0 and records the same identity
# fields they did; "vendor" is Glimmer's own defaults, for repeated sampled held-out evaluation, and adds a
# "sampling" field. The serve decodes speculatively only at temperature 0, so sampled rollouts are slower.
SAMPLING_PRESETS = {
    "greedy": {"temperature": 0},
    "vendor": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
}


def sampling_from_env() -> str:
    name = os.environ.get("HARNESS2_MOJO_SAMPLING", "greedy")
    if name not in SAMPLING_PRESETS:
        raise ValueError(f"HARNESS2_MOJO_SAMPLING must be one of {sorted(SAMPLING_PRESETS)}, not {name!r}")
    return name


def steer_from_env():
    """The host's steer for every model call of a rollout (the serve's `nomos.steer`), or None. The broker adds it."""
    raw = os.environ.get("HARNESS2_MOJO_STEER")
    if not raw:
        return None
    try:
        return validate_steer(json.loads(raw))
    except ValueError as exc:
        raise ValueError(f"HARNESS2_MOJO_STEER: {exc}") from None


def opener_from_env():
    """(text, sha256) of the opener placed before the task in the same user message, or None. The file must hash to
    HARNESS2_MOJO_OPENER_SHA256, so the text a rollout used is the text that was registered."""
    path = os.environ.get("HARNESS2_MOJO_OPENER_FILE")
    if not path:
        return None
    pinned = os.environ.get("HARNESS2_MOJO_OPENER_SHA256", "")
    raw = Path(path).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != pinned:
        raise ValueError(f"HARNESS2_MOJO_OPENER_SHA256: {path} hashes to {got}, not {pinned or 'an unset pin'}")
    return raw.decode("utf-8"), got


def prepare_agent(work: Path, sampling: str = "greedy"):
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
                            "contextWindow": CONTEXT_WINDOW,
                            "maxTokens": OUTPUT_TOKENS,
                            "samplingParams": dict(SAMPLING_PRESETS[sampling]),
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
        sampling = sampling_from_env()
        identity = {
            "task": task.sha256,
            "harness": hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest(),
            "model": MODEL,
            "temperature": SAMPLING_PRESETS[sampling]["temperature"],
            "thinking": "high",
            "sandbox": "bwrap-model-only-v1",
            "runtime": self.sandbox.identity(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "budgets": {
                "model_calls": MODEL_CALL_LIMIT,
                "context_window": CONTEXT_WINDOW,
                "output_tokens": OUTPUT_TOKENS,
            },
        }
        if sampling != "greedy":
            identity["sampling"] = {"preset": sampling, **SAMPLING_PRESETS[sampling]}
        steer, opener = steer_from_env(), opener_from_env()
        if steer is not None:
            identity["steer"] = steer
        if opener is not None:
            identity["opener_sha256"] = opener[1]
        if run_dir.exists():
            if (run_dir / "failed.json").is_file():
                saved = load_failed(run_dir)
                if saved["identity"] != identity:
                    raise ValueError("failed rollout identity changed")
                raise RecordedRolloutFailure(run_dir, saved["reason"])
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
        prepare_agent(work, sampling)
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
            task.instructions if opener is None else opener[0] + "\n\n" + task.instructions,
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
            {
                "identity": identity,
                "argv": args,
                "stdin": "DEVNULL",
                "max_model_calls": MODEL_CALL_LIMIT,
                "context_window": CONTEXT_WINDOW,
            },
        )
        with tempfile.TemporaryDirectory(prefix="h2-model-") as private:
            socket = Path(private) / "model.sock"
            with self.broker(socket, max_calls=MODEL_CALL_LIMIT, **({} if steer is None else {"steer": steer})) as broker:
                result = self.sandbox.run(
                    work, args, timeout=timeout, broker=socket, output_limit=16 * 1024 * 1024
                )
                calls = broker.calls
                broker_error = getattr(broker, "last_failure", None)
        (run_dir / "pi.jsonl").write_text(result.stdout)
        (run_dir / "stderr.txt").write_text(result.stderr)
        usage = stream_usage(run_dir / "pi.jsonl")
        if self.sandbox.identity() != identity["runtime"]:
            raise RuntimeError("runtime changed during rollout; not completed")
        assistants = assistant_messages(run_dir)
        terminal_ok = bool(assistants) and assistants[-1].get("stopReason") in ("stop", "length")
        if (
            result.reason
            or result.returncode != 0
            or not usage["settled"]
            or usage["aborted"]
            or not terminal_ok
        ):
            process = {
                key: value
                for key, value in asdict(result).items()
                if key not in ("stdout", "stderr")
            }
            reason = failure_reason(
                process, calls, MODEL_CALL_LIMIT, usage, assistants, broker_error
            )
            write_json(
                run_dir / "failed.json",
                {
                    "v": 2,
                    "identity": identity,
                    "process": process,
                    "model_calls": calls,
                    "broker_error": broker_error,
                    "reason": reason,
                    "outputs": {
                        name: hashlib.sha256(
                            file_bytes(run_dir / name, limit=16 * 1024 * 1024)
                        ).hexdigest()
                        for name in ("launch.json", "pi.jsonl", "stderr.txt")
                    },
                },
            )
            saved = load_failed(run_dir)
            raise RecordedRolloutFailure(run_dir, saved["reason"])
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
