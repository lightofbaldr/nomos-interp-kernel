"""Shared helpers for driving the Claude Code and Codex CLIs headlessly.

Both the improver channels (`core.proposer`) and the judge bridge (`cli.judge_bridge`) build
their argv, environment and result parsing here, so the two agree on flags, on how a plan
usage limit looks, and on what counts as an empty reply.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

NESTED_ENV_KEYS = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT")
NESTED_ENV_PREFIXES = ("CLAUDE_CODE_",)

# Isolation without `--bare`: `--bare` would also refuse the OAuth login (API key only).
CLAUDE_ISOLATION_FLAGS = ("--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands")

_USAGE_LIMIT = re.compile(
    r"usage[ _-]?limit|hit your limit|reached your limit|weekly limit|quota"
    r"|\bresets?\s+(?:at|in)\b",
    re.I,
)
SYSTEM_ARG_MAX = 120_000
_RATE_LIMIT = re.compile(r"rate[ _-]?limit|too many requests|\b429\b|overloaded", re.I)
_RETRY_AFTER = re.compile(r"(?:retry[ _-]?after|try again in)\D{0,8}(\d+)\s*(?:s\b|sec|second)", re.I)


def strip_nested_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop the variables a parent Claude Code session exports, so a child `claude -p` is fresh."""
    return {
        k: v
        for k, v in env.items()
        if k not in NESTED_ENV_KEYS and not k.startswith(NESTED_ENV_PREFIXES)
    }


def claude_argv(
    *,
    model: str,
    effort: str,
    system: str = "",
    tools: str | None = None,
    json_schema: str | None = None,
    skip_permissions: bool = False,
    isolation: str = "flags",
    binary: str | None = None,
) -> list[str]:
    """`claude -p` argv: JSON result, the model and effort, and isolation from user settings."""
    argv = [binary or shutil.which("claude") or "claude", "-p", "--output-format", "json"]
    # `--tools` is variadic: it goes before the single-valued flags so it can never run to
    # the end of argv (where it would swallow a positional prompt).
    if tools is not None:
        argv += ["--tools", tools]
    if skip_permissions:
        argv.append("--dangerously-skip-permissions")
    argv += ["--model", model, "--effort", effort]
    argv += ["--bare"] if isolation == "bare" else list(CLAUDE_ISOLATION_FLAGS)
    if system:
        argv += ["--append-system-prompt", system]
    if json_schema:
        argv += ["--json-schema", json_schema]
    return argv


def codex_argv(
    *,
    model: str,
    effort: str,
    cwd: Path,
    last_message: Path,
    sandbox: str = "full",
    schema_file: Path | None = None,
    images: Sequence[Path] = (),
    binary: str | None = None,
) -> list[str]:
    """`codex exec` argv; the prompt itself travels on stdin (`-`), never in argv."""
    argv = [
        binary or shutil.which("codex") or "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--json",
        "-C",
        str(cwd),
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-o",
        str(last_message),
    ]
    if sandbox == "full":
        argv.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        argv += ["-s", sandbox]
    if schema_file is not None:
        argv += ["--output-schema", str(schema_file)]
    for img in images:
        argv += ["-i", str(img)]
    argv.append("-")
    return argv


@dataclass
class CliReply:
    """What one headless CLI call produced."""

    text: str = ""
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str = ""
    limited: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


def parse_json_object(stdout: str) -> dict:
    """The claude CLI's one JSON object, tolerating banners and trailing noise around it.

    Not for opencode stdout, which is a JSONL event stream.
    """
    s = (stdout or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError:
                pass
    return {"result": s}


def parse_claude_result(stdout: str, returncode: int, stderr: str) -> CliReply:
    """Fold `claude -p --output-format json` output into a CliReply."""
    payload = parse_json_object(stdout)
    structured = payload.get("structured_output")
    if isinstance(structured, (dict, list)):
        text = json.dumps(structured)
    else:
        text = str(payload.get("result") or payload.get("text") or "")
    error = ""
    if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
        error = f"CLI error: {payload.get('subtype')} {text[:300]}".strip()
    elif returncode != 0 and not text.strip():
        error = f"rc={returncode}: {(stderr or '')[-300:]}"
    reply = CliReply(
        text="" if error else text,
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        usage=dict(payload.get("usage") or {}),
        error=error,
    )
    # A limit is a property of a FAILED call: a retry notice on stderr beside a good answer
    # must not turn that answer into a 429.
    reply.limited = bool(error) and classify_failure(error or stderr or "") is not None
    return reply


def _codex_events(stdout: str) -> list[dict]:
    events = []
    for ln in (stdout or "").replace("\x00", "").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def parse_codex_result(last_message: Path, stdout: str, returncode: int, stderr: str) -> CliReply:
    """Fold a `codex exec --json -o <file>` run into a CliReply."""
    text = ""
    try:
        text = Path(last_message).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    usage: dict = {}
    errors: list[str] = []
    for ev in _codex_events(stdout):
        et = ev.get("type", "")
        item = ev.get("item") if isinstance(ev.get("item"), dict) else None
        if item and item.get("type") == "agent_message" and not text.strip():
            txt = item.get("text")
            if isinstance(txt, str) and txt.strip():
                text = txt
        if item and item.get("type") == "error":
            errors.append(str(item.get("message") or ""))
        if et == "turn.completed" and isinstance(ev.get("usage"), dict):
            for k, v in ev["usage"].items():
                try:
                    usage[k] = usage.get(k, 0) + int(v or 0)
                except (TypeError, ValueError):
                    pass
        if et in ("turn.failed", "error"):
            err = ev.get("error")
            msg = err.get("message") if isinstance(err, dict) else err
            errors.append(str(msg or ev.get("message") or "turn failed"))
    error = "; ".join(e for e in errors if e)[-400:]
    if not error and returncode != 0 and not text.strip():
        error = f"rc={returncode}: {(stderr or '')[-300:]}"
    reply = CliReply(text="" if error else text, usage=usage, error=error)
    reply.limited = bool(error) and classify_failure(error or stderr or "") is not None
    return reply


def classify_failure(text: str) -> str | None:
    """'usage_limit' (wait it out), 'rate_limit' (short backoff) or None."""
    if not text:
        return None
    if _USAGE_LIMIT.search(text):
        return "usage_limit"
    if _RATE_LIMIT.search(text):
        return "rate_limit"
    return None


def retry_after_seconds(text: str) -> int | None:
    """A retry hint the CLI wrote into its error, in seconds, when there is one."""
    m = _RETRY_AFTER.search(text or "")
    return int(m.group(1)) if m else None


def codex_system_block(system: str, mission: str) -> str:
    """Codex has no system-prompt flag: carry the system text at the top of the prompt."""
    return f"# SYSTEM INSTRUCTIONS\n\n{system}\n\n# END\n\n{mission}"


def claude_system_args(system: str, mission: str) -> tuple[str, str]:
    """(value for --append-system-prompt, stdin): system text beyond SYSTEM_ARG_MAX moves
    onto stdin as a block, because an argv entry that large fails with E2BIG."""
    if system and len(system) > SYSTEM_ARG_MAX:
        return (
            "Follow the SYSTEM INSTRUCTIONS block at the top of the message.",
            codex_system_block(system, mission),
        )
    return system, mission


__all__ = [
    "CLAUDE_ISOLATION_FLAGS",
    "CliReply",
    "SYSTEM_ARG_MAX",
    "claude_argv",
    "claude_system_args",
    "classify_failure",
    "codex_argv",
    "codex_system_block",
    "parse_claude_result",
    "parse_codex_result",
    "parse_json_object",
    "retry_after_seconds",
    "strip_nested_env",
]
