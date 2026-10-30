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
from ..benchmarks.mojo_pi_runner import harness_tree, load_completed, write_json


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
        for path in sorted(root.rglob("complete.json")):
            done = load_completed(path.parent)
            grade_path = path.parent / "mojo-grade.json"
            if not grade_path.exists():
                rows.append(
                    {
                        "run": str(path.parent.relative_to(root)),
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
                    "run": str(path.parent.relative_to(root)),
                    "status": truth["status"],
                    "ground_truth": truth["passed"],
                    "oracle": truth["oracle"],
                    "muse_quality": obj["muse_quality"],
                }
            )
        print(json.dumps({"rows": rows, "quality_not_in_ground_truth": True}, indent=2))
        return 0
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
        run = adapter.execute(task, args.harness, f"evaluate-{args.split}-{digest[:12]}/{task}")
        rows.append({"task": task, "score": adapter.score(run, task)})
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
