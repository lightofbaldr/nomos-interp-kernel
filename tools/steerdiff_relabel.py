"""Correct the channel labels of a capture's run documents (a capture made before 10-26's priming fix).
  pixi run --no-install python3 tools/steerdiff_relabel.py ~/nomos_data/steering-diff/RUN
A channel is a pure function of the token sequence: the ATEM parser's state before each token. A scenario whose prefill
reaches past the original prompt left the capture's parser unprimed, so the replay's thinking tail read "template"
and an answer scenario's tokens read "template" as well. The readout values are untouched; only the labels change.
Every rewritten run document keeps its original in RUN/SCENARIO/runs.orig/, and RUN/relabel.json lists each change."""
from __future__ import annotations

import collections
import copy
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pyhost"))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def relabel(doc: dict, onset: int, factory):
    """(a corrected copy, how many records changed). factory(primer) returns channel_of(token), primed with the prefix
    tokens past the original prompt (positions onset + 1 on)."""
    channel_of = factory(doc["prompt_ids"][onset + 1:])
    channels = [channel_of(int(t)) for t in doc["tokens"]]
    new, changed, P = copy.deepcopy(doc), 0, len(doc["prompt_ids"])
    for r in new["records"]:
        if r["source"] == "generated":
            ch = channels[int(r["pos"]) - P]
            changed += ch != r["channel"]
            r["channel"] = ch
    return new, changed


def _put(path: str, obj) -> None:
    tmp = path + ".tmp-relabel"
    with open(tmp, "x") as f:
        json.dump(obj, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def main(argv=None) -> int:
    out = os.path.expanduser((argv or sys.argv[1:])[0])
    from transformers import AutoTokenizer
    import serve
    from serve_protocol import load_serve_protocol
    import steerdiff_capture as C
    with open(os.path.join(out, "identity.json")) as f:
        ident = json.load(f)
    tok = AutoTokenizer.from_pretrained(ident["identity"]["route"]["TOK_DIR"])
    protocol = load_serve_protocol(tok, ident["identity"]["model_id"])
    onsets = {sc["key"]: sc["onset"] for sc in ident.get("scenarios", [])}
    log = []
    for key in ident["prompts"]:
        d = os.path.join(out, key, "runs")
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            with open(path) as f:
                doc = json.load(f)
            onset = onsets.get(key)
            onset = len(doc["prompt_ids"]) - 1 if onset is None else int(onset)
            new, changed = relabel(doc, onset, lambda primer: C.make_channel_of(serve, protocol, tok, primer))
            if changed:
                os.makedirs(os.path.join(out, key, "runs.orig"), exist_ok=True)
                shutil.copy2(path, os.path.join(out, key, "runs.orig", name))
                _put(path, new)
            count = lambda x: dict(collections.Counter(r["channel"] for r in x["records"]   # noqa: E731
                                                       if r["source"] == "generated"))
            log.append({"file": os.path.join(key, "runs", name), "changed": changed, "before": count(doc),
                        "after": count(new)})
            print(f"{key}/{name}: {changed} records relabelled {count(doc)} -> {count(new)}", flush=True)
    with open(os.path.join(out, "relabel.json"), "x") as f:
        json.dump(log, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
