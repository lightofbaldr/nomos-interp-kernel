"""Steering diff UI bundle: one capture's comparisons for the Baseline | Steered | Difference view.
  pixi run --no-install python3 tools/steerdiff_export.py ~/nomos_data/steering-diff/RUN [--display-k 8]
Writes RUN/ui/index.json and RUN/ui/PROMPT/ARM.MODE.json. Every comparison file carries steerdiff.compare's own document
(nomos.steerdiff/1), so the view only renders and never re-derives a delta or a bound. vocab decodes every token id
the view can show (the prompt, both arms' tokens and every candidate), text holds each arm's reasoning and answer,
and scenario carries the capture's own scenario record with its provenance (None for a capture without one)."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pyhost"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import steerdiff          # noqa: E402
import steerdiff_report   # noqa: E402

SCHEMA = "nomos.steerdiff.ui/1"


def _channel(tok, doc, channel: str) -> str:
    ids = [r["token"] for r in doc["records"] if r["source"] == "generated" and r["channel"] == channel]
    return tok.decode(ids, skip_special_tokens=True)


def _write(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _comparison(tok, base: dict, doc: dict, display_k: int, head: dict) -> tuple[dict, dict]:
    """One comparison file (head's fields first) and steerdiff.compare's document for it."""
    c = steerdiff.compare(base, doc, display_k)
    ids = set(base["prompt_ids"]) | set(base["tokens"]) | set(doc["tokens"])
    for p in c["positions"]:
        for layer in p["workspace"] or ():
            ids.update(x["id"] for x in layer["candidates"])
    return dict(head, comparison=c, vocab={str(i): tok.decode([i]) for i in sorted(ids)},
                text={side: {ch: _channel(tok, r, ch) for ch in ("reasoning", "answer")}
                      for side, r in (("baseline", base), ("steered", doc))}), c


def export(out: str, tok, display_k: int = 8, pairs=None, labels_path: str | None = None) -> str:
    """Write the bundle under out/ui (a fresh directory) and return its path. A sampled capture compares each arm with
    base at the same sample; pairs ("ARM.sK") selects among those, and its labels.json (or labels_path, e.g. a labeled
    run the capture reproduces) is carried into each comparison as the labeler's reading of each side."""
    ident, gates = steerdiff_report._identity_gates(out)
    ui = os.path.join(out, "ui")
    # Each scenario as the capture recorded it (its provenance included), titled; {} for a capture without them.
    scenarios = {sc["key"]: dict(sc, title=ident["prompts"].get(sc["key"])) for sc in ident.get("scenarios", [])}
    comparisons, labels_src = [], None
    if ident.get("sampling"):
        _, _, runs = steerdiff_report.load_sampled(out)
        labels_src = labels_path or os.path.join(out, "labels.json")
        lab = None
        if os.path.exists(labels_src):
            with open(labels_src) as f:
                lab = json.load(f)
        else:
            labels_src = None
        want = set(pairs) if pairs else None
        os.makedirs(ui)
        for prompt in ident["prompts"]:
            for arm in ident["arms"]:
                for k in sorted(k for (p, n, m, k) in runs if p == prompt and n == arm["name"] and m == "free"):
                    tag = f"{arm['name']}.s{k}"
                    base = runs.get((prompt, "base", "free", k))
                    if arm["name"] == "base" or base is None or (want is not None and tag not in want):
                        continue
                    doc = runs[(prompt, arm["name"], "free", k)]
                    head = {"schema": SCHEMA, "prompt": prompt, "scenario": scenarios.get(prompt), "arm": doc["arm"],
                            "mode": "free", "sample": k}
                    if lab is not None:
                        head["labels"] = {"by": lab.get("by"), "baseline": lab["labels"].get(f"base.s{k}"),
                                          "steered": lab["labels"].get(tag)}
                    rel = f"{prompt}/{tag}.free.json"
                    body, c = _comparison(tok, base, doc, display_k, head)
                    _write(os.path.join(ui, rel), body)
                    comparisons.append({"prompt": prompt, "arm": arm["name"], "mode": "free", "sample": k,
                                        "path": rel, "divergence": c["divergence"]})
    else:
        if pairs:
            raise ValueError("pairs selects among a sampled capture's comparisons; this capture is not sampled")
        _, _, runs = steerdiff_report.load(out)
        os.makedirs(ui)
        for prompt in ident["prompts"]:
            for mode in ("free", "matched"):
                base = runs.get((prompt, "base", mode))
                if base is None:
                    continue
                for arm in ident["arms"]:
                    doc = runs.get((prompt, arm["name"], mode))
                    if arm["name"] == "base" or doc is None:
                        continue
                    rel = f"{prompt}/{arm['name']}.{mode}.json"
                    body, c = _comparison(tok, base, doc, display_k, {
                        "schema": SCHEMA, "prompt": prompt, "scenario": scenarios.get(prompt), "arm": arm,
                        "mode": mode})
                    _write(os.path.join(ui, rel), body)
                    comparisons.append({"prompt": prompt, "arm": arm["name"], "mode": mode, "path": rel,
                                        "divergence": c["divergence"]})
    _write(os.path.join(ui, "index.json"), {
        "schema": SCHEMA, "run": os.path.basename(os.path.normpath(out)), "identity": ident["identity"],
        "settings": ident["settings"], "prompts": ident["prompts"], "scenarios": scenarios, "arms": ident["arms"],
        "gates": gates, "labels_source": labels_src,
        "display_k": display_k, "comparisons": comparisons})
    return ui


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--display-k", type=int, default=8)
    ap.add_argument("--pairs", default="", help="sampled capture: only these comparisons, comma-separated ARM.sK")
    ap.add_argument("--labels", default=None, help="sampled capture: a labels.json to carry (default RUN/labels.json)")
    args = ap.parse_args(argv)
    out = os.path.expanduser(args.out)
    from transformers import AutoTokenizer
    with open(os.path.join(out, "identity.json")) as f:
        tok_dir = json.load(f)["identity"]["route"]["TOK_DIR"]
    pairs = [p for p in args.pairs.split(",") if p] or None
    print("wrote", export(out, AutoTokenizer.from_pretrained(tok_dir), args.display_k, pairs,
                          os.path.expanduser(args.labels) if args.labels else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
