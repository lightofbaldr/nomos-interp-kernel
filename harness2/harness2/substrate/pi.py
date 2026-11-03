"""PiSubstrate: the Pi coding agent (pi.dev) as a solver substrate.

Pi runs one task per `pi --mode json` process under an isolated agent directory, with every
discovery path switched off, so a rollout sees exactly the harness under test. The editable
surface keeps OpenCode's four always-on files (composed into one `--append-system-prompt`
file), Agent-Skills skills loaded with `--skill`, helper scripts in the workspace, and Pi
TypeScript extensions loaded with `-e` as the lever that acts inside the tool loop.
Sub-agents are not available on this substrate.

    materialise()  -> staging dir + `_rendered/` (APPEND_SYSTEM.md, .pi/skills, .pi/extensions,
                      scripts/) that the benchmark launcher merges into the task workspace
    pi_argv()      -> the reference `pi --mode json` command (launchers build their own)
    render_events()-> the JSON event stream in the shared [ACTION]/[OBSERVATION] grammar
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

PI_SURFACE = SurfaceSpec(
    name="pi",
    always_on=(
        "systemprompt.md",
        "guardrails.md",
        "LongTermMEMORY.md",
        "tool_descriptions/tool_guidance.md",
    ),
    always_on_max=24_000,
    always_on_cap_reason="every token here competes with the agent's own output budget",
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to systemprompt.md or "
        'guardrails.md, e.g. "When <the situation this skill handles>, read the '
        "`{name}` skill's SKILL.md and follow it.\""
    ),
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, read the `{name}` skill's SKILL.md and follow it.\n",
    subagent_requires_mode=False,
    plugin_dir="extensions",
    hooks_file=None,
    compile_skill_scripts=True,
    reject_empty_always_on=False,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated sub-agents are NOT available on the Pi substrate. "
            "A sub_agents/<name>/AGENT.md fails validation; do not add one. -->\n",
        ),
        (
            "extensions/README.md",
            "<!-- c8: Pi extensions -- the only lever that acts INSIDE the tool loop, so the "
            "model cannot skip it. Add one as extensions/<name>.ts exporting "
            "`default function (pi: ExtensionAPI)`; `pi.on(\"tool_call\", ...)` may mutate "
            "`event.input` or return {block: true, reason}; `pi.on(\"tool_result\", ...)` may "
            "return replacement {content}; `pi.registerTool(defineTool({...}))` adds a tool. "
            "Imports: `Type` from \"@earendil-works/pi-ai\", `defineTool` and `ExtensionAPI` "
            "from \"@earendil-works/pi-coding-agent\". -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: reusable workflows. Add one as skills/<name>/SKILL.md with YAML "
            "frontmatter `name:` (matching the directory) and `description:` (REQUIRED, or the "
            "skill is not loaded). Pi advertises name+description; the agent reads the body "
            "with the `read` tool on demand. Helpers go in skills/<name>/scripts/. -->\n",
        ),
    ),
)


RENDERED = "_rendered"
# Runtime-owned extension every rollout loads first: fences the file tools to the workspace.
GUARD_EXTENSION = Path(__file__).resolve().parent / "pi_guard.ts"
APPEND_SYSTEM_REL = ".harness2/APPEND_SYSTEM.md"
ISOLATION_FLAGS = (
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-mcp",
    "--no-context-files",
    "--no-approve",
    "--offline",
)
MIN_PI_VERSION = (1, 3, 0)
MIN_NODE_VERSION = (22, 19, 0)
SETUP_REMEDY = "run `harness2-setup <bench> --substrate pi`"

_TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _interp(text: str, ctx: Mapping[str, str]) -> str:
    """Substitute {{ name }} tokens only; single braces in prose stay untouched."""
    return _TOKEN.sub(lambda m: str(ctx.get(m.group(1), "")), text)


def _strip_comments(text: str) -> str:
    """Drop HTML comment blocks, so a comment-only component renders to nothing."""
    return _COMMENT.sub("", text).strip()


def _read_component(staged: Path, rel: str) -> str:
    p = staged / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def _list_scripts(staged: Path) -> list[Path]:
    d = staged / "scripts"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def _script_doc(p: Path) -> str:
    """First line of the script's module docstring, if any."""
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "helper script"
    m = re.search(r'^\s*(?:"""|\'\'\')(.+)', txt)
    return m.group(1).strip()[:120] if m else "helper script"


def compose_append_system(staged: Path, ctx: Mapping[str, str]) -> str:
    """c1 prose + c6 guardrails + c4 tool guidance + c3 memory + a c7 script note.

    The same composition JobBench appends to OpenCode's AGENTS.md; on Pi it is the file
    passed with `--append-system-prompt`.
    """
    staged = Path(staged)
    parts: list[str] = []
    sp = _strip_comments(_interp(_read_component(staged, "systemprompt.md"), ctx))
    if sp:
        parts.append(sp)
    gr = _strip_comments(_interp(_read_component(staged, "guardrails.md"), ctx))
    if gr:
        parts.append("# Operating Guardrails\n\n" + gr)
    tg = _strip_comments(
        _interp(_read_component(staged, "tool_descriptions/tool_guidance.md"), ctx)
    )
    if tg:
        parts.append(tg)
    mem = _strip_comments(_interp(_read_component(staged, "LongTermMEMORY.md"), ctx))
    if mem:
        parts.append("# Long-Term Memory\n\n" + mem)
    scripts = _list_scripts(staged)
    if scripts:
        listing = "\n".join(f"- `python scripts/{s.name}` — {_script_doc(s)}" for s in scripts)
        parts.append(
            "# Helper scripts (run via Bash)\n\nDeterministic helpers are available "
            "in `./scripts`. Prefer them over hand-rolling the same logic:\n\n" + listing
        )
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


@dataclass(frozen=True)
class RenderedHarness:
    """What `render_pi_workspace` put under a render dir, and what the launcher must pass."""

    root: Path
    append_system: Path | None
    skills: tuple[Path, ...]
    extensions: tuple[Path, ...]
    scripts: tuple[str, ...]


def render_pi_workspace(staged: Path, render_dir: Path, ctx: Mapping[str, str]) -> RenderedHarness:
    """Materialise a harness tree as Pi-loadable files under `render_dir`."""
    staged, rd = Path(staged), Path(render_dir)
    if rd.exists():
        shutil.rmtree(rd)
    rd.mkdir(parents=True)
    body = compose_append_system(staged, ctx)
    append: Path | None = None
    if body.strip():
        append = rd / APPEND_SYSTEM_REL
        append.parent.mkdir(parents=True, exist_ok=True)
        append.write_text(body, encoding="utf-8")
    skills: list[Path] = []
    skills_dir = staged / "skills"
    if skills_dir.is_dir():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            dest = rd / ".pi" / "skills" / skill_md.parent.name
            shutil.copytree(skill_md.parent, dest)
            skills.append(dest)
            # Pi discovers the skill under .pi/skills/, but the improver is told (and tells
            # the agent) that a helper lives at `skills/<name>/scripts/<file>` relative to the
            # workspace; mirror the helpers there so the promised path exists at runtime.
            helpers = skill_md.parent / "scripts"
            if helpers.is_dir():
                shutil.copytree(
                    helpers, rd / "skills" / skill_md.parent.name / "scripts", dirs_exist_ok=True
                )
    extensions: list[Path] = []
    ext_dir = staged / "extensions"
    if ext_dir.is_dir():
        for ts in sorted(ext_dir.glob("*.ts")):
            if ts.name.startswith("_"):
                continue
            dest = rd / ".pi" / "extensions" / ts.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ts, dest)
            extensions.append(dest)
    scripts: list[str] = []
    for s in _list_scripts(staged):
        (rd / "scripts").mkdir(exist_ok=True)
        shutil.copy(s, rd / "scripts" / s.name)
        scripts.append(s.name)
    return RenderedHarness(rd, append, tuple(skills), tuple(extensions), tuple(scripts))


def load_rendered(render_dir: Path) -> RenderedHarness:
    """Rebuild the RenderedHarness record from a render dir on disk (the runner's view)."""
    rd = Path(render_dir)
    append = rd / APPEND_SYSTEM_REL
    skills = tuple(
        sorted(p.parent for p in (rd / ".pi" / "skills").glob("*/SKILL.md"))
        if (rd / ".pi" / "skills").is_dir()
        else ()
    )
    extensions = tuple(
        sorted((rd / ".pi" / "extensions").glob("*.ts"))
        if (rd / ".pi" / "extensions").is_dir()
        else ()
    )
    scripts = tuple(
        sorted(p.name for p in (rd / "scripts").glob("*.py")) if (rd / "scripts").is_dir() else ()
    )
    return RenderedHarness(rd, append if append.is_file() else None, skills, extensions, scripts)


def harvest_output(output_dir: Path, final_dir: Path) -> int:
    """Copy the regular files under output/ into the store; symlinks and special files are
    skipped so a rollout cannot smuggle a link to something outside its workspace into the
    judged deliverables. Returns the number of files copied."""
    output_dir, final_dir = Path(output_dir), Path(final_dir)
    root = output_dir.resolve()
    copied = 0
    for dirpath, dirnames, filenames in os.walk(output_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
        for name in filenames:
            src = Path(dirpath) / name
            if src.is_symlink() or not src.is_file() or name.startswith("."):
                continue
            if not src.resolve().is_relative_to(root):
                continue
            dest = final_dir / src.relative_to(output_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    return copied


# The rollout environment is an allowlist: Pi reads provider keys from the linked
# models.json/auth.json, so secrets in the parent shell never reach a rollout.
# HARNESS2_PI_PASS_ENV names extra variables to pass through.
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR")
ENV_ALLOW_PREFIXES = ("LC_", "XDG_", "PI_")


def allowlisted_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The secret-free subset of `base` (default os.environ) a rollout may see."""
    from ..core.clitools import strip_nested_env

    source = strip_nested_env(os.environ if base is None else base)
    passed = {v.strip() for v in (source.get("HARNESS2_PI_PASS_ENV") or os.environ.get("HARNESS2_PI_PASS_ENV") or "").split(",") if v.strip()}
    return {
        k: v
        for k, v in source.items()
        if k in ENV_ALLOW or k.startswith(ENV_ALLOW_PREFIXES) or k in passed
    }


_PI_SEM = threading.BoundedSemaphore(max(1, int(config.PI_CONCURRENCY)))


class PiSubstrate:
    """Pi behind the one Substrate interface; the launcher body is benchmark-supplied."""

    name = "pi"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return PI_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Copy the tree to a staging dir and render `_rendered/` beside it."""
        if spec.install_mode != "pi_workspace":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the Pi mode; this substrate has "
                f"exactly one: 'pi_workspace'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(PI_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a Pi harness tree (no always-on file present)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-pi-"))
        try:
            shutil.copytree(tree_dir, staged, dirs_exist_ok=True)
            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    p = staged / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")
            render_pi_workspace(
                staged,
                staged / RENDERED,
                {k: v for k, v in ctx.items() if not k.startswith("file:")},
            )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={"install_mode": "pi_workspace", "rendered": str(staged / RENDERED)},
        )

    def pi_argv(
        self,
        prompt: str,
        workspace: Path,
        model: str,
        *,
        thinking: str,
        tools: str,
        session_dir: Path | None = None,
        name: str,
        rendered: RenderedHarness | None,
        binary: str | None = None,
    ) -> list[str]:
        """The reference `pi --mode json` argv; the real launchers build their own.

        Every discovery path is off; the harness arrives only through the explicit
        `--append-system-prompt`, `--skill` and `-e` paths. The prompt follows `--`.
        """
        session_dir = session_dir or Path(workspace) / ".harness2" / "pi_sessions"
        argv = [
            binary or config.PI_BIN,
            "--mode",
            "json",
            "--session-dir",
            str(session_dir),
            "--name",
            name,
            "--model",
            model,
            "--thinking",
            thinking,
            "--tools",
            tools,
            *ISOLATION_FLAGS,
        ]
        if rendered is not None:
            if rendered.append_system is not None:
                argv += ["--append-system-prompt", str(rendered.append_system)]
            for skill in rendered.skills:
                argv += ["--skill", str(skill)]
            for ext in rendered.extensions:
                argv += ["-e", str(ext)]
        argv += ["--", prompt]
        return argv

    def pi_env(self, base_env: Mapping[str, str], *, agent_dir: Path) -> dict[str, str]:
        """The isolated agent directory, no version check, no telemetry."""
        env = dict(base_env)
        env.pop("PI_CODING_AGENT_SESSION_DIR", None)
        env["PI_CODING_AGENT_DIR"] = str(agent_dir)
        env["PI_SKIP_VERSION_CHECK"] = "1"
        env["PI_TELEMETRY"] = "0"
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "PiSubstrate was constructed without a launcher body; the benchmark adapter "
                "must supply one (it owns the workspace layout and read-back locations)"
            )
        with _PI_SEM:
            return self._launcher(
                RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)),
                install,
            )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """The binaries, the isolated agent dir and the solver model; starts no model call."""
        binary = shutil.which(config.PI_BIN) or (
            config.PI_BIN if Path(config.PI_BIN).is_file() else None
        )
        if not binary:
            raise RuntimeError(
                f"pi not found ({config.PI_BIN!r}); install Pi (https://pi.dev) or set "
                f"HARNESS2_PI_BIN"
            )
        version = _version(subprocess.run([binary, "--version"], **_CAP).stdout)
        if version < MIN_PI_VERSION:
            raise RuntimeError(
                f"pi {'.'.join(map(str, version))} is older than "
                f"{'.'.join(map(str, MIN_PI_VERSION))}; run `pi update`"
            )
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node not found; Pi needs Node.js 22.19 or newer on PATH")
        nv = _version(subprocess.run([node, "--version"], **_CAP).stdout)
        if nv < MIN_NODE_VERSION:
            raise RuntimeError(
                f"node {'.'.join(map(str, nv))} is older than 22.19, which Pi requires"
            )
        agent_dir = Path(config.PI_AGENT_DIR)
        if not (agent_dir / "settings.json").is_file():
            raise RuntimeError(f"isolated Pi agent dir missing at {agent_dir}; {SETUP_REMEDY}")
        for name in ("models.json", "auth.json"):
            if not (agent_dir / name).exists():
                raise RuntimeError(
                    f"{agent_dir / name} is missing or a dangling link; {SETUP_REMEDY} (it links "
                    f"your ~/.pi/agent/{name})"
                )
        model = config.SOLVER_MODEL_PI
        provider, _, model_id = model.partition("/")
        r = subprocess.run(
            [binary, "--offline", "--list-models", model_id],
            env=self.pi_env(os.environ, agent_dir=agent_dir),
            **_CAP,
        )
        rows = [ln.split() for ln in (r.stdout or "").splitlines()]
        if not any(row[:2] == [provider, model_id] for row in rows if len(row) >= 2):
            raise RuntimeError(
                f"Pi cannot resolve {model} under {agent_dir} (pi --list-models shows no "
                f"'{provider} {model_id}' row); {SETUP_REMEDY}, which links models.json/auth.json "
                f"and refreshes the catalog with `PI_CODING_AGENT_DIR={agent_dir} pi update "
                f"--models`; then check the provider has a key"
            )


_CAP = {"capture_output": True, "text": True, "timeout": 120}


def _version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return (0,)
    return tuple(int(x or 0) for x in m.groups())


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def _text_of_content(content) -> str:
    """The text blocks of a Pi content list (or a bare string), joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return ""
    out = []
    for c in content:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
            out.append(c["text"])
    return "\n".join(t for t in out if t)


def parse_events(path: Path) -> list[dict]:
    """The JSON-mode records, LF-split; a torn last line or a non-JSON line is skipped."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    events: list[dict] = []
    for ln in raw.split("\n"):
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


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str:
    """Render a `pi --mode json` stream into the shared grammar.

    Actions come from assistant `message_end` tool-call blocks and observations from
    `tool_execution_end`, so a `toolResult` message is never rendered twice. A stream that
    never reached `agent_settled` ends with an [ERROR] line instead of raising.
    """
    try:
        events = parse_events(Path(path))
    except OSError as e:
        return f"(failed to read trajectory: {e})"
    if not events:
        return "(no parseable events in trajectory)"
    lines: list[str] = []
    settled = False
    for ev in events:
        et = ev.get("type", "")
        if et == "message_end":
            m = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            role, content = m.get("role"), m.get("content")
            if role == "user":
                txt = _text_of_content(content)
                if txt.strip():
                    lines.append(f"[USER] {_clip(txt, obs_trunc)}")
            elif role == "assistant":
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    t = c.get("type")
                    if t == "thinking":
                        txt = str(c.get("thinking") or "")
                        if txt.strip():
                            lines.append(f"[THINKING] {_clip(txt, obs_trunc)}")
                    elif t == "text":
                        txt = str(c.get("text") or "")
                        if txt.strip():
                            lines.append(f"[ASSISTANT] {_clip(txt, obs_trunc)}")
                    elif t == "toolCall":
                        args = json.dumps(c.get("arguments"), ensure_ascii=False, default=str)
                        lines.append(f"[ACTION] {c.get('name') or 'tool'}({_clip(args, args_trunc)})")
        elif et == "tool_execution_end":
            name = ev.get("toolName") or "tool"
            result = ev.get("result")
            txt = _text_of_content(result.get("content")) if isinstance(result, dict) else str(result or "")
            tag = "OBSERVATION ERROR" if ev.get("isError") else "OBSERVATION"
            lines.append(f"[{tag}] {name}: {_clip(txt, obs_trunc)}")
        elif et == "auto_retry_end" and not ev.get("success", True):
            lines.append(f"[ERROR] {_clip(ev.get('finalError') or 'retries exhausted', 1000)}")
        elif et in ("compaction_start", "compaction_end"):
            lines.append(f"[HARNESS] compaction {ev.get('reason', '')}".rstrip())
        elif et == "agent_settled":
            settled = True
            if ev.get("aborted"):
                lines.append("[ERROR] aborted")
    if not settled:
        lines.append("[ERROR] stream ended before agent_settled")
    rendered = "\n".join(lines).strip()
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def stream_usage(path: Path) -> dict:
    """Token, cost, turn and tool-call totals of one stream, for metrics."""
    out = {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cost_usd": 0.0,
        "turns": 0,
        "tool_calls": 0,
        "settled": False,
        "aborted": False,
    }
    for ev in parse_events(Path(path)):
        et = ev.get("type", "")
        if et == "message_end":
            m = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            if m.get("role") == "assistant" and isinstance(m.get("usage"), dict):
                u = m["usage"]
                for k in ("input", "output", "reasoning"):
                    try:
                        out[k] += int(u.get(k) or 0)
                    except (TypeError, ValueError):
                        pass
                cost = u.get("cost")
                total = cost.get("total") if isinstance(cost, dict) else cost
                try:
                    out["cost_usd"] += float(total or 0.0)
                except (TypeError, ValueError):
                    pass
        elif et == "turn_end":
            out["turns"] += 1
        elif et == "tool_execution_end":
            out["tool_calls"] += 1
        elif et == "agent_settled":
            out["settled"] = True
            out["aborted"] = bool(ev.get("aborted"))
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def final_message(path: Path) -> str:
    """The last non-empty assistant text block: the run's closing summary."""
    last = ""
    for ev in parse_events(Path(path)):
        if ev.get("type") != "message_end":
            continue
        m = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        if m.get("role") != "assistant":
            continue
        for c in m.get("content") if isinstance(m.get("content"), list) else []:
            if isinstance(c, dict) and c.get("type") == "text" and str(c.get("text") or "").strip():
                last = str(c["text"])
    return last


__all__ = [
    "APPEND_SYSTEM_REL",
    "ENV_ALLOW",
    "ENV_ALLOW_PREFIXES",
    "allowlisted_env",
    "GUARD_EXTENSION",
    "ISOLATION_FLAGS",
    "PI_SURFACE",
    "PiSubstrate",
    "RENDERED",
    "RenderedHarness",
    "compose_append_system",
    "final_message",
    "harvest_output",
    "load_rendered",
    "parse_events",
    "render_events",
    "render_pi_workspace",
    "stream_usage",
]
