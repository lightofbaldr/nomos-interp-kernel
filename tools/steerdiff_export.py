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


def export(out: str, tok, display_k: int = 8) -> str:
    """Write the bundle under out/ui (a fresh directory) and return its path."""
    ident, gates, runs = steerdiff_report.load(out)
    ui = os.path.join(out, "ui")
    os.makedirs(ui)
    # Each scenario as the capture recorded it (its provenance included), titled; {} for a capture without them.
    scenarios = {sc["key"]: dict(sc, title=ident["prompts"].get(sc["key"])) for sc in ident.get("scenarios", [])}
    comparisons = []
    for prompt in ident["prompts"]:
        for mode in ("free", "matched"):
            base = runs.get((prompt, "base", mode))
            if base is None:
                continue
            for arm in ident["arms"]:
                doc = runs.get((prompt, arm["name"], mode))
                if arm["name"] == "base" or doc is None:
                    continue
                c = steerdiff.compare(base, doc, display_k)
                ids = set(base["prompt_ids"]) | set(base["tokens"]) | set(doc["tokens"])
                for p in c["positions"]:
                    for layer in p["workspace"] or ():
                        ids.update(x["id"] for x in layer["candidates"])
                rel = f"{prompt}/{arm['name']}.{mode}.json"
                _write(os.path.join(ui, rel), {
                    "schema": SCHEMA, "prompt": prompt, "scenario": scenarios.get(prompt), "arm": arm, "mode": mode,
                    "comparison": c,
                    "vocab": {str(i): tok.decode([i]) for i in sorted(ids)},
                    "text": {side: {ch: _channel(tok, r, ch) for ch in ("reasoning", "answer")}
                             for side, r in (("baseline", base), ("steered", doc))}})
                comparisons.append({"prompt": prompt, "arm": arm["name"], "mode": mode, "path": rel,
                                    "divergence": c["divergence"]})
    _write(os.path.join(ui, "index.json"), {
        "schema": SCHEMA, "run": os.path.basename(os.path.normpath(out)), "identity": ident["identity"],
        "settings": ident["settings"], "prompts": ident["prompts"], "scenarios": scenarios, "arms": ident["arms"],
        "gates": gates,
        "display_k": display_k, "comparisons": comparisons})
    return ui


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--display-k", type=int, default=8)
    args = ap.parse_args(argv)
    out = os.path.expanduser(args.out)
    from transformers import AutoTokenizer
    with open(os.path.join(out, "identity.json")) as f:
        tok_dir = json.load(f)["identity"]["route"]["TOK_DIR"]
    print("wrote", export(out, AutoTokenizer.from_pretrained(tok_dir), args.display_k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
