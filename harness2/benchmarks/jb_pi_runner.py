"""JobBench Pi launcher: stage a task workspace, run Pi in it, keep the artifacts.

The Pi twin of `jb_opencode_runner.py`: same environment contract, same workspace staging,
same retry loop and the same outputs (`model_traj/<label>/*.jsonl` event streams,
`model_output/<label>/` deliverables). The rendered harness (`_rendered/` from
`PiSubstrate.materialise`) is merged into the workspace and handed to Pi through explicit
`--append-system-prompt`, `--skill` and `-e` paths under an isolated agent directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from harness2.substrate.pi import (
    GUARD_EXTENSION,
    PiSubstrate,
    RenderedHarness,
    allowlisted_env,
    harvest_output,
    load_rendered,
)

TEMP_BASE = Path("/tmp/hevo_ws")
TRAJ_DIR_NAME = "model_traj"


PROMPT = """=== TASK FOLDER ===
{task_folder}

=== INSTRUCTIONS ===
1. Read the TASK_INSTRUCTIONS.txt file in the task folder above
2. Based on the Reference Files section in TASK_INSTRUCTIONS.txt, read the corresponding files from the same task folder using appropriate tools.
3. Complete the task as specified in TASK_INSTRUCTIONS.txt
4. Only save the final deliverables to the output directory specified below. Do not save any intermediate or temporary files.

=== OUTPUT DIRECTORY ===
{output_dir}

IMPORTANT:
- All reference files are in the task folder: {task_folder}
- Only save the final deliverables to the output directory {output_dir}. Do not save any intermediate or temporary files.
- You MUST only access files within {workspace} or search online for new reference files if you find needed. Do NOT access any files or directories in this system outside of this path.
- If you encounter ambiguous or conflicting information, analyze the conflict, explain your reasoning, and justify the approach you choose.
- If a file cannot be read directly (e.g., .xlsx, .docx, .db, .pptx), use appropriate tools, MCP servers, or code to extract and process its contents."""


@dataclass(frozen=True)
class Settings:
    task_ids: tuple[str, ...]
    label: str
    render_dir: Path
    tasks_base: Path
    pi_bin: str
    agent_dir: Path
    model_id: str
    thinking: str
    tools: str
    timeout_per_task: str
    max_retries: int
    log_dir: Path


def _settings() -> Settings:
    """Resolve the environment contract the adapter writes; a bad one exits non-zero."""
    env = os.environ
    required = ("MODEL_ID", "LABEL", "HARNESS_RENDER_DIR", "TASK_IDS", "TASKS_BASE_DIR", "PI_AGENT_DIR")
    missing = [n for n in required if not env.get(n)]
    if missing:
        raise SystemExit(f"[ERROR] unset: {', '.join(missing)}")
    render_dir = Path(env["HARNESS_RENDER_DIR"])
    if not render_dir.is_dir():
        raise SystemExit(f"[ERROR] HARNESS_RENDER_DIR is not a directory: {render_dir}")
    tasks_base = Path(env["TASKS_BASE_DIR"])
    return Settings(
        task_ids=tuple(env["TASK_IDS"].split()),
        label=env["LABEL"],
        render_dir=render_dir,
        tasks_base=tasks_base,
        pi_bin=env.get("PI_BIN") or "pi",
        agent_dir=Path(env["PI_AGENT_DIR"]),
        model_id=env["MODEL_ID"],
        thinking=env.get("PI_THINKING") or "high",
        tools=env.get("PI_TOOLS") or "+grep,+find,+ls",
        timeout_per_task=env.get("TIMEOUT_PER_TASK") or "2400",
        max_retries=int(env.get("MAX_RETRIES") or 2),
        log_dir=tasks_base / ".harness2-logs",
    )


def _hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _log(log_file: Path, line: str) -> None:
    print(line, flush=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _reset_task(task_dir: Path, workspace: Path) -> None:
    """Restore a pristine task_folder and an empty output/ before a retry."""
    shutil.rmtree(workspace / "task_folder", ignore_errors=True)
    shutil.rmtree(workspace / "output", ignore_errors=True)
    shutil.copytree(task_dir, workspace / "task_folder")
    (workspace / "output").mkdir(parents=True, exist_ok=True)


def _stage(task_dir: Path, render_dir: Path, workspace: Path) -> RenderedHarness:
    """A fresh workspace: task inputs, an empty output/, and the rendered harness merged in."""
    shutil.rmtree(workspace, ignore_errors=True)
    (workspace / "output").mkdir(parents=True)
    shutil.copytree(task_dir, workspace / "task_folder")
    shutil.copytree(render_dir, workspace, dirs_exist_ok=True)
    return load_rendered(workspace)


def _command(
    cfg: Settings, workspace: Path, prompt: str, rendered: RenderedHarness, session_dir: Path, name: str
) -> list[str]:
    guarded = replace(rendered, extensions=(GUARD_EXTENSION, *rendered.extensions))
    return [
        "timeout",
        "--kill-after=30s",
        cfg.timeout_per_task,
        *PiSubstrate().pi_argv(
            prompt,
            workspace,
            cfg.model_id,
            thinking=cfg.thinking,
            tools=cfg.tools,
            session_dir=session_dir,
            name=name,
            rendered=guarded,
            binary=cfg.pi_bin,
        ),
    ]


def _env(cfg: Settings, *, workspace: Path) -> dict[str, str]:
    """A minimal, secret-free environment for one rollout."""
    env = PiSubstrate().pi_env(allowlisted_env(), agent_dir=cfg.agent_dir)
    env["HARNESS2_WORKSPACE_ROOT"] = str(workspace)
    return env


_harvest = harvest_output  # shared with the LAB runner


def _attempt(
    cfg: Settings,
    workspace: Path,
    traj_file: Path,
    log_file: Path,
    task_name: str,
    attempt: int,
    rendered: RenderedHarness,
) -> int:
    """Run Pi once; its JSON event stream lands in `traj_file`, everything else in the log."""
    prompt = PROMPT.format(
        task_folder=workspace / "task_folder",
        output_dir=workspace / "output",
        workspace=workspace,
    )
    name = f"{task_name} | {cfg.label} | a{attempt}"
    session_dir = traj_file.parent / "pi_sessions"
    handle, raw = tempfile.mkstemp(prefix=f".{task_name}_a{attempt}.stdout.", dir=traj_file.parent)
    os.close(handle)
    with open(raw, "w", encoding="utf-8") as out, log_file.open("a", encoding="utf-8") as err:
        code = subprocess.run(
            _command(cfg, workspace, prompt, rendered, session_dir, name),
            cwd=workspace,
            env=_env(cfg, workspace=workspace),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        ).returncode
    with open(raw, encoding="utf-8", errors="replace") as src:
        traj_file.write_text("".join(ln for ln in src if ln.startswith("{")), encoding="utf-8")
    os.unlink(raw)
    return code


def run_one(cfg: Settings, task_id: str) -> bool:
    """Roll one task; True only when deliverables landed in `model_output/<label>/`.

    A rollout that dies on auth or the provider still writes a trajectory, so the caller
    must not read one as evidence that the agent ran: `main` turns a False here into a
    non-zero exit, which is what stops the adapter marking an output-less attempt complete.
    """
    task_parent = cfg.tasks_base / task_id
    task_dir = task_parent / "task_folder"
    task_name = task_id.replace("/", "_")
    final_output_dir = task_parent / "model_output" / cfg.label
    traj_dir = task_parent / TRAJ_DIR_NAME / cfg.label
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = cfg.log_dir / f"hevo_{task_name}_{cfg.label}_{_stamp()}.log"

    if not task_dir.is_dir():
        _log(log_file, f"[ERROR] no task_folder: {task_dir}")
        return False
    if _nonempty(final_output_dir):
        print(f"[{_hms()}] SKIP (done): {task_id} ({cfg.label})", flush=True)
        return True
    traj_dir.mkdir(parents=True, exist_ok=True)

    TEMP_BASE.mkdir(parents=True, exist_ok=True)
    workspace = TEMP_BASE / f"{task_name}_{cfg.label}_{os.getpid()}"
    output_dir = workspace / "output"
    rendered = _stage(task_dir, cfg.render_dir, workspace)

    for attempt in range(1, cfg.max_retries + 1):
        if attempt > 1:
            _reset_task(task_dir, workspace)
        traj_file = traj_dir / f"{task_name}_{cfg.label}_attempt{attempt}_{_stamp()}.jsonl"
        _log(log_file, f"[{_hms()}] run {task_id} ({cfg.label}) attempt {attempt} -> {traj_file}")
        code = _attempt(cfg, workspace, traj_file, log_file, task_name, attempt, rendered)
        if _nonempty(output_dir):
            break
        if code in (124, 137):
            _log(log_file, f"[{_hms()}] TIMEOUT {task_id} attempt {attempt}")

    delivered = _nonempty(output_dir) and _harvest(output_dir, final_output_dir) > 0
    if delivered:
        _log(log_file, f"[{_hms()}] output -> {final_output_dir}")
    else:
        _log(log_file, f"[{_hms()}] NO OUTPUT for {task_id} ({cfg.label})")
    shutil.rmtree(workspace, ignore_errors=True)
    return delivered and _nonempty(final_output_dir)


def main() -> int:
    cfg = _settings()
    print(f"=== harness eval (pi): LABEL={cfg.label} MODEL={cfg.model_id} ===", flush=True)
    print(f"render: {cfg.render_dir}", flush=True)
    failed = [task_id for task_id in cfg.task_ids if not run_one(cfg, task_id)]
    print(f"=== harness eval done: {cfg.label} ===", flush=True)
    if failed:
        print(f"no output for: {', '.join(failed)}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
