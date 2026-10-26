"""Steering diff report: a markdown summary of one capture directory (tools/steerdiff_capture.py --out).
  pixi run --no-install python3 tools/steerdiff_report.py ~/nomos_data/steering-diff/RUN
Writes RUN/report.md. Descriptive only: workspace scores are the kernel's J-lens readouts (lens, final norm, NVFP4
head), not probabilities and not calibrated across layers; a changed readout does not show that its concept caused the
reply."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pyhost"))
import steerdiff   # noqa: E402

DISPLAY_K = 8
SHOW_LAYERS = (36, 48)          # the first and last workspace layers, to keep the report short
EMO_SHOW = (24, 29, 51)         # the intervention layer, one downstream, the last


def load(out: str):
    with open(os.path.join(out, "identity.json")) as f:
        ident = json.load(f)
    gates = {}
    if os.path.exists(os.path.join(out, "gates.json")):
        with open(os.path.join(out, "gates.json")) as f:
            gates = json.load(f)
    runs = {}
    for prompt in ident["prompts"]:
        d = os.path.join(out, prompt, "runs")
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else ():
            with open(os.path.join(d, name)) as f:
                doc = json.load(f)
            runs[(prompt, doc["arm"]["name"], doc["mode"])] = doc
    return ident, gates, runs


def channel_text(tok, doc, channel: str, limit: int = 420) -> str:
    ids = [r["token"] for r in doc["records"] if r["source"] == "generated" and r["channel"] == channel]
    text = tok.decode(ids, skip_special_tokens=True).strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _channels(runs, prompt) -> list:
    """The reply channels (reasoning, answer) present among a prompt's generated records, in that order."""
    seen = {r["channel"] for (p, _, _), doc in runs.items() if p == prompt
            for r in doc["records"] if r["source"] == "generated"}
    return [ch for ch in ("reasoning", "answer") if ch in seen]


def _signed(names, row, n_up: int = 4, n_down: int = 3) -> str:
    """The largest rises and the largest falls, each list holding only its own sign."""
    order = sorted(range(len(names)), key=lambda e: row[e])
    up = [e for e in order[::-1] if row[e] > 0][:n_up]
    down = [e for e in order if row[e] < 0][:n_down]
    fmt = lambda es: ", ".join(f"{names[e]} {row[e]:+.3g}" for e in es) or "(none)"   # noqa: E731
    return f"up {fmt(up)}; down {fmt(down)}"


def label(tok, i: int) -> str:
    return repr(tok.decode([int(i)]))


def report(out: str, tok, display_k: int = DISPLAY_K) -> str:
    ident, gates, runs = load(out)
    idn = ident["identity"]
    L = ["# Workspace steering diff: " + os.path.basename(os.path.normpath(out)), "",
         "Descriptive only. Workspace scores are the kernel's J-lens readouts (lens, final norm, exact NVFP4 head), not "
         "probabilities, and not calibrated across layers. A changed readout does not show that its concept caused the "
         "reply. Matched input replay feeds every arm the same token ids (base's own reply, or a recorded episode); free "
         "generation lets each arm write its own.",
         "",
         f"- Library {idn['library_sha256'][:16]}... (build id {idn['library_build_id']}), directions "
         f"{idn['directions'][:12]}..., lens {idn['lens'][:12]}..., tokenizer {idn['tokenizer_sha256'][:12]}...",
         f"- Schedule: {idn['schedule']}. Steering at layer {idn['steer_layer']} from the last prompt token.",
         f"- System message: {ident['system']!r}. max_new {ident['max_new']}. Provenance: {ident['provenance']}.",
         f"- Workspace layers {ident['settings']['workspace']['layers']}, top {ident['settings']['workspace']['k']} sent, "
         f"top {display_k} displayed. Emotion layers {ident['settings']['emotion']['layers']}.", ""]
    names = ident["settings"]["emotion"]["names"]
    emo_layers = ident["settings"]["emotion"]["layers"]
    ws_layers = ident["settings"]["workspace"]["layers"]
    show = [l for l in SHOW_LAYERS if l in ws_layers]          # only layers this capture configured
    emo_show = [l for l in EMO_SHOW if l in emo_layers]
    for prompt, text in ident["prompts"].items():
        L += [f"## {prompt}: {text}", ""]
        g = gates.get(prompt, {})
        if g:
            L.append("Gates: " + ", ".join(f"{k} **{'PASS' if v['pass'] else 'FAIL'}**" + (f" ({v['detail']})"
                                                                                          if v["detail"] else "")
                                           for k, v in g.items()) + ".")
            L.append("")
        base = runs.get((prompt, "base", "free"))
        if base is not None:                     # a matched-only scenario has no free table
            L += ["### Free generation", "", "| arm | first difference from base | stop | answer (first 420 chars) |",
                  "|---|---|---|---|"]
            for arm in ident["arms"]:
                doc = runs.get((prompt, arm["name"], "free"))
                if doc is None:
                    continue
                if arm["name"] == "base":
                    div = "-"
                else:
                    c = steerdiff.compare(base, doc, display_k)
                    d = c["divergence"]
                    div = "identical" if d is None else (
                        f"token {d['index']}: {label(tok, d['baseline_token']) if d['baseline_token'] is not None else 'end'}"
                        f" -> {label(tok, d['steered_token']) if d['steered_token'] is not None else 'end'}")
                ans = channel_text(tok, doc, "answer") or "(no answer within the budget) reasoning: " + channel_text(
                    tok, doc, "reasoning")
                L.append(f"| {arm['name']} | {div} | {doc['saw_stop']} | {ans.replace('|', '/')} |")
        L += ["", "### Lexicons (positions whose displayed top 8 holds a lexicon word / positions with a workspace)", "",
              "| arm | mode | " + " | ".join(f"{lex} L{l}" for lex in ident["lexicons"] for l in show) + " |",
              "|---|---|" + "---|" * (len(ident["lexicons"]) * len(show))]
        for mode in ("free", "matched"):
            for arm in ident["arms"]:
                doc = runs.get((prompt, arm["name"], mode))
                if doc is None:
                    continue
                lx = doc["lexicons"]
                cells = [f"{lx[lex][ws_layers.index(l)]}/{lx['positions'][ws_layers.index(l)]}"
                         for lex in ident["lexicons"] for l in show]
                L.append(f"| {arm['name']} | {mode} | " + " | ".join(cells) + " |")
        chans = _channels(runs, prompt)
        if len(chans) > 1:                       # thinking and answering: the decision and the reply, read apart
            L += ["", "### Lexicons by channel", "",
                  "| arm | mode | channel | " + " | ".join(f"{lex} L{l}" for lex in ident["lexicons"] for l in show) + " |",
                  "|---|---|---|" + "---|" * (len(ident["lexicons"]) * len(show))]
            for mode in ("free", "matched"):
                for arm in ident["arms"]:
                    doc = runs.get((prompt, arm["name"], mode))
                    if doc is None:
                        continue
                    for ch in chans:
                        lx = steerdiff.lexicon_counts(doc, ident["lexicons"], display_k, channels={ch})
                        cells = [f"{lx[lex][ws_layers.index(l)]}/{lx['positions'][ws_layers.index(l)]}"
                                 for lex in ident["lexicons"] for l in show]
                        L.append(f"| {arm['name']} | {mode} | {ch} | " + " | ".join(cells) + " |")
        ref = runs.get((prompt, "base", "matched"))
        if ref is None:
            continue
        L += ["", f"### Matched input replay (every arm consumed the same reference ids ({ref['reference']}))", ""]
        for arm in ident["arms"]:
            doc = runs.get((prompt, arm["name"], "matched"))
            if doc is None or arm["name"] == "base" or arm["kind"] in (None, "zero"):
                continue
            c = steerdiff.compare(ref, doc, display_k)
            L.append(f"**{arm['name']}** ({arm['kind']}, s {arm['s']})")
            L.append("")
            for layer in show:
                mv = steerdiff.movers(c, ws_layers.index(layer))
                up = [m for m in mv if m["mean_delta"] is not None and m["mean_delta"] > 0 and m["count"] >= 8][:6]
                down = [m for m in reversed(mv) if m["mean_delta"] is not None and m["mean_delta"] < 0
                        and m["count"] >= 8][:6]
                entered = sorted((m for m in mv if m["entered"]), key=lambda m: -m["entered"])[:6]
                L.append(f"- L{layer} up: " + ", ".join(f"{label(tok, m['id'])} {m['mean_delta']:+.3g} ({m['count']})"
                                                        for m in up))
                L.append(f"- L{layer} down: " + ", ".join(f"{label(tok, m['id'])} {m['mean_delta']:+.3g} ({m['count']})"
                                                          for m in down))
                L.append(f"- L{layer} entered the top {display_k}: " + ", ".join(
                    f"{label(tok, m['id'])} x{m['entered']}" for m in entered))
            for ch in (chans if len(chans) > 1 else [None]):
                means = steerdiff.emotion_means(c, channels=None if ch is None else {ch})
                if means is None:
                    continue
                for layer in emo_show:
                    L.append(f"- emotion L{layer}{'' if ch is None else f' ({ch})'}: "
                             + _signed(names, means[emo_layers.index(layer)]))
            L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    args = ap.parse_args(argv)
    out = os.path.expanduser(args.out)
    from transformers import AutoTokenizer
    with open(os.path.join(out, "identity.json")) as f:
        tok_dir = json.load(f)["identity"]["route"]["TOK_DIR"]
    text = report(out, AutoTokenizer.from_pretrained(tok_dir))
    path = os.path.join(out, "report.md")
    with open(path, "x") as f:
        f.write(text)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
