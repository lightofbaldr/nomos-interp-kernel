"""Workspace steering diff (docs/STEERING_DIFF.md): compare two arms' nomos.readout/1 records.

- Free generation aligns positions only up to the first differing generated token; after it, each arm keeps its own
  track. Matched input replay requires both arms to have consumed exactly the same ids from the same reference run.
- A workspace score outside an arm's transmitted top k is unknown, bounded above by that arm's k-th score, and never
  zero. A rank is a rank within the transmitted top k, never a full-vocabulary rank.
- Emotion deltas are exact: every record carries all of its directions at every configured layer.
CPU only: serve records and the GPU research adapter's records go through the same code."""
from __future__ import annotations

import copy

SCHEMA = "nomos.steerdiff/1"
RUN_SCHEMA = "nomos.steerdiff.run/1"
MODES = ("free", "matched")


class SteerDiffError(ValueError):
    """A comparison the data cannot support: mismatched replays, prompts, settings or identities, or a malformed run."""


def run_doc(*, run_id, arm, mode, reference, prompt_ids, tokens, records, settings, identity) -> dict:
    """One arm's capture. tokens are the generated (free) or replayed (matched) ids in order; a record at position
    len(prompt_ids) + j carries tokens[j]. A token without a record (a stop token never fed back) is allowed."""
    if mode not in MODES:
        raise SteerDiffError(f"mode {mode!r} is not one of {MODES}")
    if mode == "matched" and not reference:
        raise SteerDiffError("a matched replay names the reference run it replayed")
    if mode == "free" and reference is not None:
        raise SteerDiffError("a free generation has no reference run")
    prompt_ids, tokens = [int(t) for t in prompt_ids], [int(t) for t in tokens]
    P, seen = len(prompt_ids), set()
    for r in records:
        p = int(r["pos"])
        if p in seen:
            raise SteerDiffError(f"two records at position {p}")
        seen.add(p)
        if r["source"] == "prompt":
            if not 0 <= p < P or int(r["token"]) != prompt_ids[p]:
                raise SteerDiffError(f"the prompt record at {p} carries token {r['token']}, not the prompt's")
        elif r["source"] == "generated":
            j = p - P
            if not 0 <= j < len(tokens) or int(r["token"]) != tokens[j]:
                raise SteerDiffError(f"the generated record at {p} carries token {r['token']}, not the run's")
        else:
            raise SteerDiffError(f"the record at {p} has source {r['source']!r}")
    return {"schema": RUN_SCHEMA, "run_id": str(run_id), "arm": copy.deepcopy(arm), "mode": mode,
            "reference": reference, "prompt_ids": prompt_ids, "tokens": tokens,
            "records": sorted(copy.deepcopy(records), key=lambda r: int(r["pos"])),
            "settings": copy.deepcopy(settings), "identity": copy.deepcopy(identity)}


def workspace_candidates(top_a, top_b, display_k: int = 8) -> list[dict]:
    """One layer at one position: the union of both arms' displayed top display_k, baseline's order first. top_a and
    top_b are [[id, score], ...] sorted as the kernel sends them (score descending, then id ascending)."""
    if not 1 <= display_k <= min(len(top_a), len(top_b)):
        raise SteerDiffError(f"display_k {display_k} must lie in [1, the transmitted top k]")
    sa = {int(i): (float(s), r + 1) for r, (i, s) in enumerate(top_a)}
    sb = {int(i): (float(s), r + 1) for r, (i, s) in enumerate(top_b)}
    floor_a, floor_b = float(top_a[-1][1]), float(top_b[-1][1])     # a token outside the list scores at most this
    shown_a = [int(i) for i, _ in top_a[:display_k]]
    shown_b = [int(i) for i, _ in top_b[:display_k]]
    out = []
    for i in shown_a + [i for i in shown_b if i not in shown_a]:
        a, b = sa.get(i), sb.get(i)
        c = {"id": i, "baseline": a[0] if a else None, "steered": b[0] if b else None,
             "baseline_rank": a[1] if a else None, "steered_rank": b[1] if b else None,
             "baseline_below": None if a else floor_a, "steered_below": None if b else floor_b,
             "delta": None, "delta_min": None, "delta_max": None,
             "status": "both" if i in shown_a and i in shown_b else ("left" if i in shown_a else "entered")}
        if a and b:
            c["delta"] = b[0] - a[0]
        elif b:
            c["delta_min"] = b[0] - floor_a
        else:
            c["delta_max"] = floor_b - a[0]
        out.append(c)
    return out


def emotion_delta(ea, eb):
    """steered - baseline per emotion layer and direction, or None when either record has no emotion entry."""
    if ea is None or eb is None:
        return None
    pa, pb = ea["proj"], eb["proj"]
    if len(pa) != len(pb) or any(len(x) != len(y) for x, y in zip(pa, pb)):
        raise SteerDiffError("emotion records of different shapes")
    return [[float(y) - float(x) for x, y in zip(ra, rb)] for ra, rb in zip(pa, pb)]


def _workspace(a, b, layers, display_k):
    wa = a["workspace"]["top"] if a.get("workspace") else None
    wb = b["workspace"]["top"] if b.get("workspace") else None
    out = []
    for i, layer in enumerate(layers):
        missing = [side for side, w in (("baseline", wa), ("steered", wb)) if w is None]
        out.append({"layer": layer, "missing": missing,
                    "candidates": [] if missing else workspace_candidates(wa[i], wb[i], display_k)})
    return out


def _side(r) -> dict:
    return {"run_id": r["run_id"], "arm": copy.deepcopy(r["arm"]), "identity": copy.deepcopy(r["identity"]),
            "tokens": list(r["tokens"])}


def compare(base: dict, steered: dict, display_k: int = 8) -> dict:
    """The Baseline | Steered | Difference document for two runs of the same mode."""
    for name, r in (("baseline", base), ("steered", steered)):
        if r.get("schema") != RUN_SCHEMA:
            raise SteerDiffError(f"the {name} is not a {RUN_SCHEMA} document")
    if base["mode"] != steered["mode"]:
        raise SteerDiffError(f"mode: a {base['mode']} run is never compared with a {steered['mode']} run")
    if base["prompt_ids"] != steered["prompt_ids"]:
        raise SteerDiffError("the arms start from different prompts: fork both from the same conversation")
    if base["settings"] != steered["settings"]:
        raise SteerDiffError("the arms' readout settings differ")
    if base["identity"] != steered["identity"]:
        raise SteerDiffError("the arms' identity records differ (library, assets or tokenizer)")
    mode, P = base["mode"], len(base["prompt_ids"])
    ta, tb = base["tokens"], steered["tokens"]
    if mode == "matched":
        if base["reference"] != steered["reference"]:
            raise SteerDiffError(f"matched replays of different reference runs ({base['reference']!r} and "
                                 f"{steered['reference']!r})")
        if ta != tb:
            raise SteerDiffError("matched replay: the arms consumed different token ids")
        d, divergence, tracks = len(ta), None, None
    else:
        d = next((j for j, (x, y) in enumerate(zip(ta, tb)) if x != y), min(len(ta), len(tb)))
        divergence = None if d == len(ta) == len(tb) else {
            "index": d, "position": P + d,
            "baseline_token": ta[d] if d < len(ta) else None, "steered_token": tb[d] if d < len(tb) else None}
        tracks = {"baseline": ta[d:], "steered": tb[d:]}
    ws = base["settings"].get("workspace") or {}
    layers = ws.get("layers") or []
    if layers and not 1 <= display_k <= int(ws["k"]):
        raise SteerDiffError(f"display_k {display_k} must lie in [1, {ws['k']}]")
    ra = {int(r["pos"]): r for r in base["records"]}
    rb = {int(r["pos"]): r for r in steered["records"]}
    positions = []
    for p in range(P + d):
        a, b = ra.get(p), rb.get(p)
        if a is None and b is None:
            continue
        one = a or b
        entry = {"pos": p, "token": int(one["token"]), "source": one["source"], "channel": one.get("channel"),
                 "missing": [side for side, r in (("baseline", a), ("steered", b)) if r is None],
                 "emotion_delta": None, "workspace": None}
        if a is not None and b is not None:
            entry["emotion_delta"] = emotion_delta(a.get("emotion"), b.get("emotion"))
            if one["source"] == "generated" and layers:
                entry["workspace"] = _workspace(a, b, layers, display_k)
        positions.append(entry)
    return {"schema": SCHEMA, "mode": mode, "reference": base["reference"] if mode == "matched" else None,
            "display_k": display_k, "P": P, "baseline": _side(base), "steered": _side(steered),
            "settings": copy.deepcopy(base["settings"]), "divergence": divergence, "tracks": tracks,
            "positions": positions}


def movers(comparison: dict, layer_index: int) -> list[dict]:
    """Per candidate token at one workspace layer, over the comparison's aligned generated positions: the mean of its
    exact deltas (None when it has none), how many exact deltas that mean averages, and how often the token entered or
    left the displayed list. Highest mean first; tokens without an exact delta come last."""
    acc = {}
    for p in comparison["positions"]:
        if not p.get("workspace"):
            continue
        for c in p["workspace"][layer_index]["candidates"]:
            a = acc.setdefault(c["id"], {"sum": 0.0, "count": 0, "entered": 0, "left": 0})
            if c["delta"] is not None:
                a["sum"] += c["delta"]
                a["count"] += 1
            a["entered"] += c["status"] == "entered"
            a["left"] += c["status"] == "left"
    out = [{"id": i, "mean_delta": a["sum"] / a["count"] if a["count"] else None, "count": a["count"],
            "entered": a["entered"], "left": a["left"]} for i, a in acc.items()]
    return sorted(out, key=lambda m: (m["mean_delta"] is None, -(m["mean_delta"] or 0.0), m["id"]))


def emotion_means(comparison: dict):
    """The mean emotion delta per layer and direction over the aligned generated positions (None without any)."""
    rows = [p["emotion_delta"] for p in comparison["positions"]
            if p["source"] == "generated" and p["emotion_delta"] is not None]
    if not rows:
        return None
    return [[sum(r[layer][e] for r in rows) / len(rows) for e in range(len(rows[0][layer]))]
            for layer in range(len(rows[0]))]


def lexicon_counts(run: dict, lexicons: dict, display_k: int = 8) -> dict:
    """Per workspace layer: how many generated positions show any of a lexicon's token ids in the displayed top
    display_k, beside how many positions had a workspace at all. Membership only, so no score arithmetic."""
    layers = (run["settings"].get("workspace") or {}).get("layers") or []
    sets = {name: {int(t) for t in ids} for name, ids in lexicons.items()}
    out = {"positions": [0] * len(layers), **{name: [0] * len(layers) for name in sets}}
    for r in run["records"]:
        if r["source"] != "generated" or not r.get("workspace"):
            continue
        for i, top in enumerate(r["workspace"]["top"]):
            shown = {int(t) for t, _ in top[:display_k]}
            out["positions"][i] += 1
            for name, ids in sets.items():
                if shown & ids:
                    out[name][i] += 1
    return out
