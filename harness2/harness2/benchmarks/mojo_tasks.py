"""Local Mojo corpus. Expected output, tests and reference stay on the host.

The two source formats are intentionally explicit: complete programs, or function
completions with the source dataset's appended test main. This is the dataset's
compile/run oracle, not a proof against a program intentionally spoofing stdout.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    kind: str
    split: str
    expect_stdout: str | None = None
    test: str | None = None
    domain: str = "mojo"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @property
    def instructions(self) -> str:
        if self.test is not None:
            return (
                self.prompt + "\nWrite only the requested definitions to /work/solution.mojo. "
                "Do not define main; the caller supplies it. Use Mojo 1.0 std imports."
            )
        return self.prompt + "\nSave the complete program to /work/solution.mojo."


class Corpus:
    def __init__(
        self,
        train: Path,
        heldout: Path,
        selection: Path | None = None,
        *,
        smoke: Path | None = None,
        seed=20261030,
        counts: dict | None = None,
    ):
        self.tasks: dict[str, Task] = {}
        self.sources: dict[str, str] = {}
        prompts: set[str] = set()
        self.seed = seed
        self.exclusions = {
            "simd_dot": "contradictory prompt; both written vector pairs give 20, stored answer 30"
        }
        # Reserve the heldout IDs/prompts before considering training candidates.
        files = [("test", heldout), ("train", train)] + ([("smoke", smoke)] if smoke else [])
        for split, path in files:
            raw = path.read_bytes()
            self.sources[split] = hashlib.sha256(raw).hexdigest()
            seen_here = set()
            for line_no, line in enumerate(raw.decode().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    task = self._task(row, split)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"{path.name}:{line_no}: invalid Mojo task") from exc
                if task.id in self.exclusions or (split == "smoke" and task.kind != "run"):
                    continue
                normalized = " ".join(task.prompt.split()).casefold()
                if task.id in seen_here:
                    raise ValueError("duplicate task id within source")
                seen_here.add(task.id)
                if split == "train" and (task.id in self.tasks or normalized in prompts):
                    self.exclusions["train:" + task.id] = (
                        "heldout ID or exact prompt takes precedence"
                    )
                    continue
                if task.id in self.tasks or normalized in prompts:
                    raise ValueError("duplicate task id or prompt across corpus/splits")
                self.tasks[task.id] = task
                prompts.add(normalized)
        if not all(any(t.split == s for t in self.tasks.values()) for s in ("train", "test")):
            raise ValueError("train and heldout must both be nonempty")
        if selection is not None:
            raw = selection.read_bytes()
            chosen = json.loads(raw)
            if not isinstance(chosen, dict) or set(chosen) != {"train", "test"}:
                raise ValueError("selection must contain train and test lists")
            keep = []
            for split in ("train", "test"):
                ids = chosen[split]
                if (
                    not isinstance(ids, list)
                    or not ids
                    or any(not isinstance(k, str) for k in ids)
                    or len(set(ids)) != len(ids)
                ):
                    raise ValueError("selection needs nonempty unique id lists")
                for key in ids:
                    if key not in self.tasks or self.tasks[key].split != split:
                        raise ValueError("selection may not move tasks between splits")
                keep.extend(ids)
            self.tasks = {k: self.tasks[k] for k in sorted(keep)}
            self.sources["selection"] = hashlib.sha256(raw).hexdigest()
        elif counts is not None:
            if type(seed) is not int or set(counts) != {split for split, _ in files}:
                raise ValueError("seed/count split mismatch")
            keep = []
            for split, count in sorted(counts.items()):
                ids = self.ids(split)
                if type(count) is not int or not 1 <= count <= len(ids):
                    raise ValueError("sample count outside available tasks")
                # A split-local generator makes heldout membership independent of train size.
                rng = random.Random(f"harness2-mojo-v1:{seed}:{split}")
                keep.extend(rng.sample(ids, count))
            self.tasks = {k: self.tasks[k] for k in sorted(keep)}

    @staticmethod
    def _task(row, split):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not ID.fullmatch(row["id"])
        ):
            raise ValueError("unsafe task id")
        prompt = row.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000:
            raise ValueError("invalid prompt")
        kind = row.get("kind")
        if kind in ("compile", "run"):
            if "test" in row or "reference" in row:
                raise ValueError("ambiguous task format")
            expected = row.get("expect_stdout")
            if kind == "run" and not isinstance(expected, str):
                raise ValueError("run tasks require expect_stdout")
            return Task(row["id"], prompt, kind, split, expected)
        if kind is None and isinstance(row.get("test"), str) and row["test"].strip():
            # The existing function-completion corpus has a raising main and prints ok.
            # No reference answer is retained in Task or passed to the executor/judge.
            if 'print("ok")' not in row["test"]:
                raise ValueError(
                    "unsupported function-test output; normalize the corpus explicitly"
                )
            return Task(row["id"], prompt, "function", split, "ok", row["test"])
        raise ValueError("unsupported task schema")

    def ids(self, split="all"):
        if split not in ("all", "train", "test", "smoke"):
            raise ValueError("unknown split")
        return sorted(k for k, t in self.tasks.items() if split == "all" or t.split == split)

    @property
    def identity(self):
        return {
            "sources": self.sources,
            "seed": self.seed,
            "selection_algorithm": "split-local Python Random.sample over sorted ids",
            "exclusions": self.exclusions,
            "selected": {s: self.ids(s) for s in ("train", "test", "smoke")},
            "tasks": {k: t.sha256 for k, t in sorted(self.tasks.items())},
        }
