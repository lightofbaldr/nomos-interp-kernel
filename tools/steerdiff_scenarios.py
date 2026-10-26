"""Steering-diff scenarios from recorded episodes: Glimmer's known lies (the examples study, 10-25) as exact token ids.
  pixi run --no-install python3 tools/steerdiff_scenarios.py --out FILE.json KEY=EPISODE.json [KEY=EPISODE.json ...]
Each episode gives two scenarios, both steered from the original prompt's last token:
- KEY-replay (matched): the recorded ids, with the prompt and all but the last `tail` thinking tokens prefilled and the
  thinking tail plus the whole answer stepped, so the decision and the lie carry workspace readouts;
- KEY-answer (free): the prompt and the recorded thinking prefilled, then each arm writes its own answer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.expanduser("~/nomos_data/emotions/glimmer/examples")
LABELS = os.path.join(EXAMPLES, "explore-lies-20261025T215252Z/emotions.json")         # Prime's lie/honest labels
CLASSIFIER = os.path.join(EXAMPLES, "analysis/final-lies/labels/key.json")          # the automatic classifier


def _lookup(item, k, cell):
    with open(LABELS) as f:
        label = next((r["label"] for r in json.load(f)["rows"] if (r["item"], r["k"], r["cell"]) == (item, k, cell)),
                     None)
    with open(CLASSIFIER) as f:
        cls = next((v.get("classifier") for v in json.load(f).values()
                    if (v["item"], v["k"], v["cell"]) == (item, k, cell)), None)
    return label, cls


def _episode(path: str):
    """(raw bytes, episode, kv ids, prompt length) for one recorded episode."""
    with open(path, "rb") as f:
        raw = f.read()
    ep = json.loads(raw)
    kv = [int(t) for t in ep["kv"]]
    reply = ep["replies"][0]
    P = len(kv) - (len(reply["tokens"]) - (1 if reply["stop"] else 0))   # a stop token is handed out, never fed
    return raw, ep, kv, P


def _source(path: str, raw: bytes, ep: dict, P: int, **extra) -> dict:
    label, cls = _lookup(ep["item"], ep["k"], ep["cell"])
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "item": ep["item"], "k": ep["k"],
            "cell": ep["cell"], "question": ep["user"], "label": label, "classifier": cls, "prompt_len": P, **extra}


def reason_scenario(path: str, key: str, max_new: int = 4096) -> dict:
    """KEY-reason (free): only the recorded prompt; steered from its last token, each arm reasons and answers on its
    own (Adam 10-26: "Steer from the prompt and let each arm reason")."""
    raw, ep, kv, P = _episode(path)
    source = _source(path, raw, ep, P, recorded_len=len(kv) - P)
    title = (f"{ep['user']} (recorded {source['label'] or 'unlabelled'}, sample {ep['k']}, cell {ep['cell']}): "
             "steered from the prompt, each arm reasons and answers")
    return {"key": f"{key}-reason", "title": title, "source": source, "prompt_ids": kv[:P], "onset": P - 1,
            "reference": None, "modes": ["free"], "max_new": int(max_new)}


def lie_scenarios(path: str, key: str, tok, protocol, channel_of, tail: int = 256, answer_max_new: int = 1024):
    """(replay, answer) for one recorded episode. channel_of(parser, token) is serve._atem_channel."""
    raw, ep, kv, P = _episode(path)
    gen = kv[P:]
    parser = protocol.stream_parser(tok)
    channels = []
    for t in gen:
        channels.append(channel_of(parser, t))
        parser.feed(t)
    a0 = channels.index("answer")
    cut = max(a0 - int(tail), 0)
    source = _source(path, raw, ep, P, answer_start=P + a0, thinking_tail=a0 - cut)
    title = f"{ep['user']} (recorded {source['label'] or 'unlabelled'}, sample {ep['k']}, cell {ep['cell']})"
    replay = {"key": f"{key}-replay", "title": title + ": the recorded lie, replayed", "source": source,
              "prompt_ids": kv[:P + cut], "onset": P - 1, "reference": gen[cut:], "modes": ["matched"],
              "max_new": None}
    answer = {"key": f"{key}-answer", "title": title + ": the recorded thinking, then each arm's own answer",
              "source": source, "prompt_ids": kv[:P + a0], "onset": P - 1, "reference": None, "modes": ["free"],
              "max_new": int(answer_max_new)}
    return replay, answer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--tail", type=int, default=256)
    ap.add_argument("--kinds", default="replay,answer", help="any of replay, answer, reason")
    ap.add_argument("--reason-max-new", type=int, default=4096)
    ap.add_argument("episodes", nargs="+", help="KEY=EPISODE.json")
    args = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, "pyhost"))
    from transformers import AutoTokenizer
    import serve
    from serve_protocol import load_serve_protocol
    tok = AutoTokenizer.from_pretrained(os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer"))
    protocol = load_serve_protocol(tok, 2)
    out = []
    for spec in args.episodes:
        key, _, path = spec.partition("=")
        kinds = set(args.kinds.split(","))
        replay, answer = lie_scenarios(os.path.expanduser(path), key, tok, protocol, serve._atem_channel, args.tail)
        out.extend(sc for kind, sc in (("replay", replay), ("answer", answer)) if kind in kinds)
        if "reason" in kinds:
            out.append(reason_scenario(os.path.expanduser(path), key, args.reason_max_new))
    with open(os.path.expanduser(args.out), "x") as f:
        json.dump(out, f)
    for sc in out:
        print(sc["key"], "prefill", len(sc["prompt_ids"]), "reference", None if sc["reference"] is None else
              len(sc["reference"]), "onset", sc["onset"], "max_new", sc["max_new"], sc["source"]["label"],
              sc["source"]["classifier"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
