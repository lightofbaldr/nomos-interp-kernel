"""Sampled lie rates per arm (steerdiff_capture.py --samples): counts of each label, the lie rate, and a two-sided
Fisher exact test of lie against everything else, each arm against base.
  python3 tools/steerdiff_rates.py RUN/labels.json [RUN/reproduction.json]
labels.json: {"labels": {"ARM.sK": {"label": "lie" | "honest" | "no_answer" | "broken", "note": ...}}}. With N samples per
arm only large shifts can reach significance; the table says what was seen, not what is general."""
from __future__ import annotations

import json
import math
import sys

LABELS = ("lie", "honest", "no_answer", "broken")


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]: the summed hypergeometric probability of every table with the
    observed margins that is no more probable than the observed one (relative tolerance 1e-7, as R and scipy use)."""
    r1, n, c1 = a + b, a + b + c + d, a + c
    total = math.comb(n, r1)

    def prob(x):
        return math.comb(c1, x) * math.comb(n - c1, r1 - x) / total
    p0 = prob(a)
    lo, hi = max(0, r1 - (n - c1)), min(r1, c1)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p0 * (1 + 1e-7)))


def rates(labels: dict, arms: list) -> list[dict]:
    """labels: {"ARM.sK": label}. One row per arm, in the given order; p_vs_base is None for base."""
    rows = []
    for arm in arms:
        got = [lab for name, lab in labels.items() if name.rsplit(".s", 1)[0] == arm]
        row = {"arm": arm, "n": len(got), **{lab: got.count(lab) for lab in LABELS}}
        rows.append(row)
    base = next((r for r in rows if r["arm"] == "base"), None)
    for r in rows:
        r["p_vs_base"] = None if r["arm"] == "base" or base is None else fisher_exact(
            r["lie"], r["n"] - r["lie"], base["lie"], base["n"] - base["lie"])
    return rows


def table(rows: list[dict]) -> str:
    out = ["| arm | n | lie | honest | no answer | broken | lie rate | Fisher p vs base |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        p = "-" if r["p_vs_base"] is None else f"{r['p_vs_base']:.3g}"
        rate = f"{r['lie']}/{r['n']}" if r["n"] else "-"
        out.append(f"| {r['arm']} | {r['n']} | {r['lie']} | {r['honest']} | {r['no_answer']} | {r['broken']} | {rate} | {p} |")
    return "\n".join(out)


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    with open(argv[0]) as f:
        doc = json.load(f)
    labels = {name: v["label"] for name, v in doc["labels"].items()}
    arms = list(dict.fromkeys(name.rsplit(".s", 1)[0] for name in labels))
    print(table(rates(labels, arms)))
    if len(argv) > 1:
        with open(argv[1]) as f:
            rep = json.load(f)
        for key, rows in rep.items():
            same = sum(1 for r in rows if r["identical"])
            print(f"\n{key}: base reproduces the recorded episode on {same}/{len(rows)} seeds; first differences "
                  f"{[r['first_difference'] for r in rows]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
