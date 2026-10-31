"""Frozen-harness heldout evaluation and side-by-side deterministic/Muse report.

This does not propose, select or reflect on the heldout tasks. An explicit harness
is mandatory. By default --dry-run is the only path allowed by the launch gate.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from ..benchmarks.mojo import MojoAdapter
from ..benchmarks.mojo_pi_runner import (
    failed_score,
    harness_tree,
    load_completed,
    load_failed,
    write_json,
)
from ..core.spec import RecordedRolloutFailure


def run_directories(root):
    """Find rollout leaves without scanning their untrusted workspaces."""
    if not root.exists():
        return

    def visit(path):
        children = list(path.iterdir())
        if any(child.is_symlink() for child in children):
            raise ValueError("linked execution tree entry")
        if path != root and (
            not children or any(child.is_file() or child.name == "workspace" for child in children)
        ):
            yield path
        else:
            for child in sorted(children):
                if child.is_dir():
                    yield from visit(child)

    yield from visit(root)


def failure_row(task, run):
    saved = load_failed(run)
    return {
        "task": task,
        "rollout": "failed",
        "model_calls": saved["model_calls"],
        "returncode": saved["process"]["returncode"],
        "reason": saved["reason"],
        "score": failed_score(saved),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("base", "evaluate", "report"))
    p.add_argument("--harness", type=Path)
    p.add_argument("--split", choices=("train", "test", "smoke"), default="test")
    p.add_argument("--split-file", type=Path)
    p.add_argument("--max-tasks", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    adapter = MojoAdapter(split_file=args.split_file)
    if args.action == "base":
        if args.dry_run:
            print(adapter.spec.runs_root / "base")
        else:
            print(adapter.base_harness())
        return 0
    if args.action == "report":
        rows = []
        root = adapter.spec.runs_root / "executions"
        for run in run_directories(root):
            name = str(run.relative_to(root))
            if (run / "failed.json").is_file():
                saved = load_failed(run)
                rows.append(
                    {
                        "run": name,
                        "status": "rollout_failed",
                        "ground_truth": False,
                        "muse_quality": None,
                        "model_calls": saved["model_calls"],
                        "returncode": saved["process"]["returncode"],
                        "reason": saved["reason"],
                    }
                )
                continue
            if not (run / "complete.json").is_file():
                rows.append(
                    {
                        "run": name,
                        "status": "incomplete",
                        "ground_truth": None,
                        "muse_quality": None,
                        "reason": "no completed or failed record",
                    }
                )
                continue
            done = load_completed(run)
            grade_path = run / "mojo-grade.json"
            if not grade_path.exists():
                rows.append(
                    {
                        "run": name,
                        "status": "not_graded",
                        "ground_truth": None,
                        "muse_quality": None,
                    }
                )
                continue
            obj = json.loads(grade_path.read_text())
            truth = obj["ground_truth"]
            if truth["task_sha256"] != done["identity"]["task"] or truth["code_sha256"] != done[
                "outputs"
            ].get("solution.mojo", hashlib.sha256(b"").hexdigest()):
                raise ValueError("grade belongs to a different task/program")
            rows.append(
                {
                    "run": name,
                    "status": truth["status"],
                    "ground_truth": truth["passed"],
                    "oracle": truth["oracle"],
                    "muse_quality": obj["muse_quality"],
                }
            )
        print(
            json.dumps(
                {
                    "rows": rows,
                    "quality_not_in_ground_truth": True,
                    "rollout_failed_count": sum(r["status"] == "rollout_failed" for r in rows),
                },
                indent=2,
            )
        )
        return 3 if any(r["status"] == "incomplete" for r in rows) else 0
    if args.harness is None:
        p.error("evaluate requires --harness (frozen, no heldout adaptation)")
    tree = harness_tree(args.harness)
    digest = hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest()
    ids = adapter.tasks(args.split)
    if args.max_tasks is not None:
        if args.max_tasks < 1:
            p.error("--max-tasks must be positive")
        ids = ids[: args.max_tasks]
    if not ids:
        p.error("requested split contains no selected tasks")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "harness_sha256": digest,
                    "split": args.split,
                    "tasks": ids,
                    "corpus": adapter.identity,
                    "calls": 0,
                },
                indent=2,
            )
        )
        return 0
    if os.environ.get("HARNESS2_MOJO_LIVE_APPROVED") != "1":
        p.error("live rollouts held pending Adam's approval through Prime")
    adapter.preflight(improver=False)
    rows = []
    for task in ids:
        if (
            hashlib.sha256(
                json.dumps(harness_tree(args.harness), sort_keys=True).encode()
            ).hexdigest()
            != digest
        ):
            raise ValueError("frozen harness changed")
        run_id = f"evaluate-{args.split}-{digest[:12]}/{task}"
        run = adapter.spec.runs_root / "executions" / run_id
        if run.exists() and not any(
            (run / name).is_file() for name in ("complete.json", "failed.json")
        ):
            rows.append(
                {
                    "task": task,
                    "rollout": "incomplete",
                    "score": None,
                    "reason": "no completed or failed record; preserved without retry",
                }
            )
        else:
            try:
                run = adapter.execute(task, args.harness, run_id)
            except RecordedRolloutFailure as exc:
                if exc.run_dir != run:
                    raise ValueError("failure points to a different rollout") from exc
                rows.append(failure_row(task, run))
            else:
                rows.append(
                    {"task": task, "rollout": "complete", "score": adapter.score(run, task)}
                )
        row = rows[-1]
        status = (
            row["score"].get("ground_truth", {}).get("status") if row["score"] else "incomplete"
        )
        print(f"{task}: rollout={row['rollout']} status={status}", flush=True)
    if (
        hashlib.sha256(json.dumps(harness_tree(args.harness), sort_keys=True).encode()).hexdigest()
        != digest
    ):
        raise ValueError("frozen harness changed")
    write_json(
        adapter.spec.runs_root / f"evaluate-{args.split}-{digest[:12]}.json",
        {"harness_sha256": digest, "split": args.split, "rows": rows, "corpus": adapter.identity},
    )
    return 0 if all(r["score"] is not None for r in rows) else 3


if __name__ == "__main__":
    raise SystemExit(main())
