"""Run one LAB task through Pi: the Pi twin of the OpenCode and Codex LAB runners.

Writes `results/<run_id>/{config.json, workspace/, pi_session.jsonl, pi_stderr.log, output/}`
and `metrics.json` LAST, so a dead model chain is never cached as a finished rollout;
`evaluation.run_eval` scores the run dir unchanged. Needs only the standard library and
harness2, so it runs under harness2's own Python; the rollout's `python3` resolves to the LAB
venv (document parsing for `scripts/read_doc.py --in host`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from harness2.substrate.pi import (
    GUARD_EXTENSION,
    PiSubstrate,
    RenderedHarness,
    allowlisted_env,
    final_message,
    harvest_output,
    load_rendered,
    parse_events,
    stream_usage,
)

ENV_DELTA_PI = """\
# Harness notes (this run)

You are running under Pi. Four details differ from the description above; where they
conflict, these win:

1. **Paths are real and absolute.** `bash` starts in the workspace root, whose absolute path
   is given in the assignment, and `$WORKSPACE_DIR`, `$DOCUMENTS_DIR` and `$OUTPUT_DIR` are
   set in the environment. `documents/`, `output/`, `scripts/` and `skills/` are
   subdirectories of the workspace. There is no path rewriting.

2. **Deliverables are not auto-routed.** Relative `write`/`edit` paths land wherever the tool
   resolves them, not in `output/`. Every deliverable must be written to `output/<filename>`
   (or its absolute path). Anything left outside `output/` is not graded.

3. **`read` does not parse binary documents.** For `.docx`, `.pdf`, `.pptx` and `.xlsx`, run

       python3 scripts/read_doc.py <path>

   which prints the extracted text. `read` is still the right tool for plain text, markdown
   and code.

4. **Skills are files.** The skills listed in your system prompt are read on demand with the
   `read` tool from the path shown; their helper scripts are under `skills/<name>/scripts/`.
"""

NUDGE = (
    "Continue the assignment to completion now. Do not ask for confirmation. "
    "If every deliverable has already been written to the output directory, "
    "reply with exactly: DONE"
)


def load_task(lab_root: Path, task_id: str) -> dict:
    """The task as LAB's `harness.run.load_task` sees it, without LAB's SDK imports."""
    parts = task_id.split("/")
    if len(parts) < 2 or ".." in parts or not all(parts):
        raise ValueError(
            f"Task name must have at least 2 parts (e.g. 'practice-area/task-slug'), got: {task_id!r}"
        )
    task_dir = Path(lab_root) / "tasks" / Path(*parts)
    config_path = task_dir / "task.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"task.json not found: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    docs_dir = task_dir / "documents"
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Documents directory not found: {docs_dir}")
    instructions = config.get("instructions")
    if not instructions:
        md = task_dir / "instructions.md"
        if not md.is_file():
            raise ValueError(f"No instructions found in task.json or {md}")
        instructions = md.read_text(encoding="utf-8")
    return {
        "name": task_id,
        "task_dir": str(task_dir),
        "docs_dir": str(docs_dir),
        "instructions": str(instructions),
    }


def base_skill_dirs(lab_root: Path) -> list[Path]:
    """LAB's own skills (docx, pptx, xlsx manuals), loaded natively on Pi."""
    return sorted(p.parent for p in (Path(lab_root) / "harness" / "skills").glob("*/SKILL.md"))


def build_prompt(instructions: str, workspace: Path) -> str:
    """The assignment message: the task instructions verbatim plus the concrete paths."""
    workspace = Path(workspace)
    return (
        f"=== WORKSPACE ===\n{workspace}\n\n"
        f"=== DOCUMENTS (read-only) ===\n{workspace / 'documents'}\n\n"
        f"=== OUTPUT DIRECTORY (deliverables go here) ===\n{workspace / 'output'}\n\n"
        f"=== ASSIGNMENT ===\n{instructions}"
    )


@dataclass(frozen=True)
class Staged:
    workspace: Path
    rendered: RenderedHarness
    base_skills: tuple[Path, ...]


def stage_workspace(run_dir: Path, task: dict, lab_root: Path, harness_render: Path | None) -> Staged:
    """A fresh workspace under the run dir: documents (read-only), output/, LAB's base skills,
    the rendered harness merged in, and the runtime's read_doc.py copied LAST."""
    lab_root = Path(lab_root)
    ws = Path(run_dir) / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "output").mkdir(parents=True)
    shutil.copytree(task["docs_dir"], ws / "documents")
    for p in (ws / "documents").rglob("*"):
        if p.is_file():
            p.chmod(0o444)
    base: list[Path] = []
    for skill in base_skill_dirs(lab_root):
        dest = ws / ".pi" / "skills" / skill.name
        shutil.copytree(skill, dest)
        base.append(dest)
        if (skill / "scripts").is_dir():
            shutil.copytree(skill / "scripts", ws / "skills" / skill.name / "scripts", dirs_exist_ok=True)
    if harness_render is not None:
        shutil.copytree(harness_render, ws, dirs_exist_ok=True)
    (ws / "scripts").mkdir(exist_ok=True)
    reader = lab_root / "opencode" / "scripts" / "read_doc.py"
    if not reader.is_file():
        raise FileNotFoundError(f"runtime document reader missing: {reader}; run harness2-setup lab")
    shutil.copy2(reader, ws / "scripts" / "read_doc.py")  # LAST: the runtime wins
    loaded = load_rendered(ws)
    skills = tuple(base) + tuple(s for s in loaded.skills if s not in base)
    return Staged(ws, replace(loaded, skills=skills), tuple(base))


def rollout_env(args, workspace: Path) -> dict[str, str]:
    """A secret-free environment where `python3` is the LAB venv and LAB's paths are set."""
    lab_root = Path(args.lab_root)
    ws = Path(workspace)
    env = allowlisted_env()
    env["PATH"] = f"{lab_root / '.venv' / 'bin'}:{env.get('PATH') or os.environ.get('PATH', '')}"
    env.update(
        {
            "LAB_DOC_PARSER": args.doc_parser,
            "LAB_BENCH_ROOT": str(lab_root),
            "WORKSPACE_DIR": str(ws),
            "DOCUMENTS_DIR": str(ws / "documents"),
            "OUTPUT_DIR": str(ws / "output"),
            "HARNESS2_WORKSPACE_ROOT": str(ws),
        }
    )
    return PiSubstrate().pi_env(env, agent_dir=Path(args.agent_dir))


def pi_command(
    args,
    workspace: Path,
    prompt: str,
    rendered: RenderedHarness,
    session_dir: Path,
    *,
    resume: Path | None = None,
    timeout: int | None = None,
) -> list[str]:
    """`timeout` + the Pi invocation with the runtime guard loaded first; `resume` continues
    a saved session with `prompt` as the next user turn."""
    guarded = replace(rendered, extensions=(GUARD_EXTENSION, *rendered.extensions))
    argv = PiSubstrate().pi_argv(
        prompt,
        Path(workspace),
        args.model,
        thinking=args.thinking,
        tools=args.tools,
        session_dir=Path(session_dir),
        name=f"{args.task} | {args.run_id}",
        rendered=guarded,
        binary=args.pi_bin,
    )
    if resume is not None:
        argv = argv[:-2] + ["--session", str(resume)] + argv[-2:]
    wall = int(max(60, timeout if timeout is not None else args.timeout))
    return ["timeout", "--kill-after=30s", str(wall), *argv]


def invoke(cmd, *, env, cwd: Path, stream_path: Path, log_path: Path, append: bool) -> int:
    """One Pi process: JSON event lines go to `stream_path`, everything else to `log_path`."""
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=".pi.stdout.", dir=stream_path.parent)
    os.close(handle)
    with open(raw, "w", encoding="utf-8") as out, open(log_path, "a", encoding="utf-8") as err:
        rc = subprocess.run(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err
        ).returncode
    with open(raw, encoding="utf-8", errors="replace") as src, open(
        stream_path, "a" if append else "w", encoding="utf-8"
    ) as dst:
        dst.writelines(ln for ln in src if ln.startswith("{"))
    os.unlink(raw)
    return rc


def _latest_session(session_dir: Path) -> Path | None:
    files = sorted(Path(session_dir).glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if Path(session_dir).is_dir() else []
    return files[-1] if files else None


def doc_coverage(events: list[dict], docs_dir: Path) -> tuple[int, int]:
    """How many task documents the agent opened: a document counts when its file name appears
    in the arguments of one of the agent's own tool calls (a `read`, a `read_doc.py` or helper
    invocation). Observations do not count, so an `ls documents/` is not "reading"."""
    docs_dir = Path(docs_dir)
    if not docs_dir.is_dir():
        return 0, 0
    names = [p.name for p in sorted(docs_dir.rglob("*")) if p.is_file()]
    args_text = []
    for ev in events:
        if ev.get("type") != "message_end":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                args_text.append(json.dumps(block.get("arguments"), ensure_ascii=False))
    joined = "\n".join(args_text)
    return sum(1 for n in names if n in joined), len(names)


def run_task(args) -> int:
    lab_root = Path(args.lab_root)
    task = load_task(lab_root, args.task)
    run_dir = Path(args.results_root) / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    harness_render = Path(args.harness_dir) if args.harness_dir else None
    staged = stage_workspace(run_dir, task, lab_root, harness_render)
    ws, rendered = staged.workspace, staged.rendered
    session_dir = run_dir / "pi_sessions"
    stream, log = run_dir / "pi_session.jsonl", run_dir / "pi_stderr.log"
    components = {
        "skills": [s.name for s in rendered.skills],
        "base_skills": [s.name for s in staged.base_skills],
        "extensions": [e.name for e in rendered.extensions],
        "scripts": list(rendered.scripts),
        "always_on_chars": len(rendered.append_system.read_text(encoding="utf-8")) if rendered.append_system else 0,
    }
    started = datetime.now(timezone.utc).isoformat()
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "runner": "pi",
                "model": args.model,
                "thinking": args.thinking,
                "tools": args.tools,
                "task": args.task,
                "run_id": args.run_id,
                "timeout": args.timeout,
                "max_continue": args.max_continue,
                "harness_dir": str(harness_render) if harness_render else None,
                "doc_parser": args.doc_parser,
                "workspace": str(ws),
                "harness_components": components,
                "started_at": started,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    env = rollout_env(args, ws)
    t0 = time.time()
    continues = 0
    rc = invoke(
        pi_command(args, ws, build_prompt(task["instructions"], ws), rendered, session_dir),
        env=env, cwd=ws, stream_path=stream, log_path=log, append=False,
    )
    for _ in range(int(args.max_continue)):
        remaining = args.timeout - (time.time() - t0)
        session = _latest_session(session_dir)
        if remaining < 60 or session is None:
            break
        delivered = any(f.is_file() for f in (ws / "output").rglob("*"))
        if delivered and final_message(stream).strip() == "DONE":
            break
        before = stream_usage(stream)["tool_calls"]
        continues += 1
        rc = invoke(
            pi_command(args, ws, NUDGE, rendered, session_dir, resume=session, timeout=int(remaining)),
            env=env, cwd=ws, stream_path=stream, log_path=log, append=True,
        )
        if stream_usage(stream)["tool_calls"] == before:
            break
    wall = time.time() - t0
    pi_rc = rc  # the last Pi process's exit status; the runner's own status is decided below

    events = parse_events(stream)
    harvested = harvest_output(ws / "output", run_dir / "output")
    if not events:
        print(
            f"[lab_pi_runner] NO parseable events in {stream}; NOT writing metrics.json (rc={pi_rc})",
            file=sys.stderr,
        )
        return 2
    usage = stream_usage(stream)
    delivered = harvested > 0
    if pi_rc in (124, 137):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(
                f"[lab_pi_runner] Pi hit the {args.timeout}s wall (exit {pi_rc}); "
                f"{'recording what it delivered' if delivered else 'nothing delivered'}\n"
            )
    if not delivered and not usage["settled"]:
        # Cut off from outside (wall, kill, crash) before the agent ended its own run, with
        # nothing to show: not a result, so no metrics.json and the rollout is re-rolled
        # rather than cached as a zero. A settled run that delivered nothing IS the agent's
        # result and is recorded like upstream's runner does.
        print(
            f"[lab_pi_runner] stream ended before agent_settled with nothing delivered "
            f"(rc={pi_rc}); NOT writing metrics.json",
            file=sys.stderr,
        )
        return pi_rc or 3
    docs_read, docs_total = doc_coverage(events, ws / "documents")
    metrics = {
        "runner": "pi",
        "model": args.model,
        "task": args.task,
        "run_id": args.run_id,
        "turn_count": usage["turns"],
        "tool_calls": usage["tool_calls"],
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "reasoning_tokens": usage["reasoning"],
        "total_tokens": usage["input"] + usage["output"],
        "wall_clock_seconds": round(wall, 1),
        "cost_usd": usage["cost_usd"],
        "finished_cleanly": pi_rc == 0,
        "exit_code": pi_rc,
        "continues": continues,
        "deliverables": sorted(str(p.relative_to(run_dir / "output")) for p in (run_dir / "output").rglob("*") if p.is_file()),
        "documents_read": docs_read,
        "total_documents": docs_total,
        "harness_components": components,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(
        f"[lab_pi_runner] {args.run_id}: pi_rc={pi_rc} turns={usage['turns']} tools={usage['tool_calls']} "
        f"continues={continues} wall={wall:.0f}s "
        f"deliverables={', '.join(metrics['deliverables']) or 'NONE'}",
        flush=True,
    )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one LAB task through Pi")
    p.add_argument("--task", required=True, help="Task ID (e.g. corporate-ma/<slug>)")
    p.add_argument("--run-id", required=True, help="Run identifier (namespaced)")
    p.add_argument("--results-root", required=True, type=Path)
    p.add_argument("--model", required=True, help="Pi provider/model")
    p.add_argument("--thinking", default="high")
    p.add_argument("--tools", default="+grep,+find,+ls")
    p.add_argument("--timeout", type=int, default=3600, help="Wall-clock budget in seconds")
    p.add_argument("--harness-dir", default=None, help="Rendered pi_workspace dir to merge in")
    p.add_argument("--agent-dir", required=True, type=Path, help="Isolated Pi agent directory")
    p.add_argument("--pi-bin", default="pi")
    p.add_argument("--lab-root", required=True, type=Path, help="The harvey-labs checkout")
    p.add_argument("--doc-parser", choices=["host", "sandbox"], default="host")
    p.add_argument("--max-continue", type=int, default=2, help="Continue-nudge resumes")
    return run_task(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
