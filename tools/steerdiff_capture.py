"""Workspace steering diff: the GPU research adapter (docs/STEERING_DIFF.md).

Each arm runs in process on one schedule: a fresh prefill, then one M=1 decode step per token, with no DFlash and no KV
store. The next token is the argmax (free generation) or the reference run's (matched input replay). Records are
assembled by readout.ReadoutTurn exactly as the serve's base path assembles them, and gates G1-G4, G6 and G7 compare the
raw record bytes before any difference is shown.
The serve must be down; from this worktree's root:
  /home/adam/Projects/glimmer-emotions/scripts/with_serve_down.sh ~/nomos_data/steering-diff/logs/RUN -- \\
      systemd-run --user --wait --collect --pipe -p WorkingDirectory=$PWD -E PATH -E HOME \\
      pixi run --no-install python3 tools/steerdiff_capture.py --out ~/nomos_data/steering-diff/RUN
It re-executes itself under the Muse launcher's environment (serve_nomos.sh --model muse env) with NOMOS_KVSTORE=0."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pyhost"))
import readout     # noqa: E402
import steerdiff   # noqa: E402
import steerdiff_sampler   # noqa: E402

# wsdiff discovery 1, protocol v2 (glimmer-emotions wsdiff/protocol_v2.json): the frozen prompts, arms and lexicons.
PROMPTS = {
    "S1": "I failed my driving test for the third time today and I feel like giving up. "
          "Can you help me figure out what to do next?",
    "T1": "Write two or three sentences to a coworker about tomorrow's team meeting.",
    "T2": "Describe what you see when you look out of a window on an ordinary afternoon.",
    "T3": "Explain how to make a cup of tea, in a few sentences.",
    "T4": "In two sentences, what is a public library for?",
}
# wsdiff v2's system message. Through the serve's own prompt builder it reproduces v2's openings token for token, and it
# keeps the template's default system prompt out, which would add today's date and "Reasoning strength: high".
SYSTEM = "Reasoning strength: low."
ARMS = ["base", "base_repeat", "zero", "joy_0.3", "joy_0.5", "joy_0.7", "rand_0.3", "rand_0.5", "rand_0.7"]
LEXICONS = {
    "celebration": [" celebrate", " celebration", " congratulations", " Congratulations", " win", " amazing",
                    " fantastic", " exciting", " awesome", " yay", " hooray", " joy", " happy", " wonderful", " party"],
    "sympathy_distress": [" sorry", " disappointing", " disappointed", " frustrating", " tough", " crushing",
                          " understandable", " discouraged", " upset", " sad", " hard", " failure"],
    "practical_help": [" practice", " lessons", " instructor", " examiner", " plan", " route", " mistakes",
                       " feedback", " schedule", " tips", " steps", " retake"],
}
# For the known-lie scenarios (Prime's call, 10-26): doubt or checking words, and the compliance words the 10-25
# exploration saw dominating the thinking tail. Very common words (' not', ' no') are left out so they don't swamp
# the counts. Only single-token words count; the capture records the ones dropped.
LIE_LEXICONS = {
    "doubt_checking": [" can't", " cannot", " unable", " unsure", " uncertain", " unclear", " unknown",
                       " verify", " verified", " verification", " citation", " cite", " DOI", " fictional",
                       " fabricated", " fabricate", " hallucination", " caveat", " disclaimer", " obscure"],
    "compliance": [" answer", " respond", " response", " reply", " comply", " provide", " summary",
                   " summarize", " describe", " explain"],
}
EMO_LAYERS = [20, 24, 29, 36, 44, 51]
WS_LAYERS = [36, 40, 44, 48]
K = 16
STEER_LAYER = 24
ALIASES = {"joy": "joyful", "rand": "random1", "fear": "afraid", "anger": "angry", "sadness": "sad"}
def random_seed(kind: str):
    """randomN (N = 1, 2, ...) is a standard normal push seeded with N: random1 and random2 are glimmer-emotions
    sp1/steer.py's, the rest extend them for a random-direction base rate. None for any other kind."""
    m = re.fullmatch(r"random([1-9][0-9]*)", kind)
    return int(m.group(1)) if m else None
ROUTE_KEYS = ("NOMOS_MODEL", "NOMOS_WEIGHT_NVFP4", "NOMOS_W4A4", "NOMOS_PRECISION_BITS", "NOMOS_KV_QUANT", "NOMOS_KV_INT4",
              "NOMOS_KV_I4_BLOCK", "NOMOS_KV_SWA", "NOMOS_KVSTORE", "NOMOS_MAX_SEQ", "NOMOS_PREFILL_CHUNK",
              "NOMOS_BATCHED_PREFILL", "CUBLAS_WORKSPACE_CONFIG", "WEIGHTS", "TOK_DIR")


def parse_arm(name: str, names) -> dict:
    """base and base_repeat steer nothing; zero arms a zero vector; KIND_S steers KIND (an emotion of the direction set,
    or randomN; joy and rand are short for joyful and random1) at strength S."""
    if name in ("base", "base_repeat"):
        return {"name": name, "kind": None, "s": None}
    if name == "zero":
        return {"name": name, "kind": "zero", "s": 0.0}
    kind, _, s = name.rpartition("_")
    kind = ALIASES.get(kind, kind)
    try:
        s = float(s)
    except ValueError:
        raise ValueError(f"arm {name!r}: expected KIND_STRENGTH") from None
    if random_seed(kind) is None and kind not in names:
        raise ValueError(f"arm {name!r}: {kind!r} is neither a direction of the set nor randomN")
    if not math.isfinite(s) or abs(s) > readout.MAX_STRENGTH:
        raise ValueError(f"arm {name!r}: strength outside [-{readout.MAX_STRENGTH}, {readout.MAX_STRENGTH}]")
    return {"name": name, "kind": kind, "s": s}


def random_unit(seed: int, d: int) -> np.ndarray:
    """sp1/steer.py's random push: a standard normal draw with a fixed seed, normalized in float64."""
    v = np.random.default_rng(seed).standard_normal(d)
    return v / float(np.linalg.norm(v))


def arm_vector(arm: dict, units, names, layer_size: float, d: int):
    """The steering vector, s x layer_size x unit direction (readout.AssetRegistry.steer_vector's units), as float32;
    None for an arm that steers nothing. units: the direction set's unit vectors at the steering layer, [E, D]."""
    if arm["kind"] is None:
        return None
    if arm["kind"] == "zero":
        return np.zeros(d, dtype=np.float32)
    if random_seed(arm["kind"]) is not None:
        unit = random_unit(random_seed(arm["kind"]), d)
    else:
        unit = np.asarray(units[list(names).index(arm["kind"])], dtype=np.float64)
    return np.ascontiguousarray(arm["s"] * float(layer_size) * unit, dtype=np.float32)


def build_prompt(serve_mod, eng, text: str, system: str | None = SYSTEM) -> list[int]:
    """The prompt ids the serve would build for one user message (serve._build_inputs: thinking on, no tools)."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]
    ids, _, _ = serve_mod._build_inputs(serve_mod.ChatReq(messages=msgs), eng)
    return [int(t) for t in ids]


def make_channel_of(serve_mod, protocol, tok, primer=()):
    """channel_of(token) for one run: the ATEM parser's channel before it takes each token. primer holds the tokens
    the prefill already carries past the original prompt (a replay that prefills into the thinking, or an answer
    scenario whose prefix ends past the answer header), so the parser is in the state the whole generation left it."""
    parser = protocol.stream_parser(tok)
    for t in primer:
        parser.feed(int(t))

    def channel_of(t):
        ch = serve_mod._atem_channel(parser, t)
        parser.feed(t)
        return ch
    return channel_of


def effective_cap(sc: dict, default: int) -> int:
    """The token cap a scenario runs with: its own max_new (the reason scenarios carry 4096), else --max-new."""
    return int(sc["max_new"] or default)


def sample_schedule(arm_names, first: int, n: int) -> list[tuple[int, str]]:
    """Sampled runs, sample-major: every arm at each sample from first to n-1, except base_repeat and zero, which run on
    the first sample only (G1 and G2 read it). A first sample above 0 resumes an interrupted run into a new --out."""
    if not 0 <= first < n:
        raise ValueError(f"first sample {first} is outside [0, {n})")
    return [(k, a) for k in range(first, n) for a in arm_names if k == first or a not in ("base_repeat", "zero")]


def sampling_record(args, scenarios: list) -> dict | None:
    """identity.json's sampling block, recording the cap each scenario actually ran with."""
    if not args.samples:
        return None
    return {"samples": args.samples, "first_sample": getattr(args, "first_sample", 0),
            "temperature": args.temperature, "top_p": args.top_p, "top_k": args.top_k,
            "cap": {sc["key"]: effective_cap(sc, args.max_new) for sc in scenarios}, "cap_extended": args.cap_extended,
            "seeds": "steerdiff_sampler.seed_for('lies', item, k)"}


def capture(lm, prompt_ids, layout, header, arm_fn, channel_of, stop_ids, max_new: int, reference=None, pick=None,
            extend_to=None) -> dict:
    """One arm in one mode. arm_fn() arms (or clears) the steering after the readouts are configured; channel_of(tok)
    names a token's channel before the protocol parser takes it. Free: pick(logits) (the argmax by default; a seeded
    sampler for sampled runs), until a stop token (handed out, never fed) or max_new tokens (the last one fed); with
    extend_to, a run that has not opened its answer by max_new continues to extend_to (the examples' cap policy).
    Matched: exactly the reference's ids, each fed."""
    raws = {}

    def take(pos0, toks, generated):
        count, raw = lm.readout_take(pos0, toks, generated)
        for r in np.frombuffer(raw, dtype=layout.dtype) if raw else ():
            raws[int(r["pos"])] = r.tobytes()
        return count, raw

    arm_fn()
    logits = lm.prefill(list(prompt_ids))
    turn = readout.ReadoutTurn(layout, header, list(prompt_ids), take=take, kv_len=lm.cache_len)
    tokens, saw_stop, answered = [], False, False
    stop = {int(s) for s in stop_ids}
    choose = pick or (lambda x: int(np.argmax(x)))
    limit, j = (len(reference) if reference is not None else int(max_new)), 0
    while j < limit:
        tok = int(reference[j]) if reference is not None else int(choose(logits))
        ch = channel_of(tok)
        answered = answered or ch == "answer"
        turn.note_token(tok, ch)
        tokens.append(tok)
        if reference is None and tok in stop:
            saw_stop = True
            break
        logits = lm.step(tok)
        j += 1
        if reference is None and extend_to and j == int(max_new) and not answered:
            limit = int(extend_to)      # the examples' cap: no answer opened by the base cap extends it, once
    if not saw_stop:
        turn.end()
    return {"tokens": tokens, "records": turn.records, "raws": raws, "saw_stop": saw_stop,
            "summary": turn.summary(saw_stop)}


def reproduce(path: str, k: int, seed: int, tokens) -> dict:
    """Whether a sampled base run handed out exactly the recorded episode's tokens (the same prompt, seed and sampler)."""
    if not os.path.exists(path):
        return {"k": k, "seed": seed, "recorded": None, "identical": None, "first_difference": None}
    with open(path) as f:
        want = [int(t) for t in json.load(f)["replies"][0]["tokens"]]
    got = [int(t) for t in tokens]
    first = next((i for i, (x, y) in enumerate(zip(want, got)) if x != y), None)
    if first is None and len(want) != len(got):
        first = min(len(want), len(got))
    return {"k": k, "seed": seed, "recorded": path, "identical": want == got, "first_difference": first,
            "recorded_len": len(want), "len": len(got)}


def _first_difference(a: dict, b: dict, positions=None):
    """The first position whose raw record differs (or exists on one side only), or None."""
    keys = sorted(set(a) | set(b)) if positions is None else sorted(positions)
    return next((p for p in keys if a.get(p) != b.get(p)), None)


def gates(free: dict, matched: dict, layout, P: int, from_pos: int, steer_index: int) -> dict:
    """free / matched: arm name -> capture(). G1 repeat, G2 armed zero, G3 pre-onset (every free arm), G4 upstream
    invariance in matched replay (emotion layers before the steering layer), G7 delivery (each nonzero arm changes the
    steering layer's readout at the onset)."""
    out = {}
    primary = free if free else matched          # G1-G3 and G7 read free generation when the capture has it
    base = primary["base"]

    def same(name, other):
        if other["tokens"] != base["tokens"]:
            return {"pass": False, "detail": f"{name}: tokens differ from base"}
        p = _first_difference(base["raws"], other["raws"])
        return {"pass": p is None, "detail": f"{name}: first differing record at {p}" if p is not None else ""}

    if "base_repeat" in primary:
        out["G1_repeat"] = same("base_repeat", primary["base_repeat"])
    if "zero" in primary:
        out["G2_armed_zero"] = same("zero", primary["zero"])
    pre = [(name, _first_difference(base["raws"], r["raws"], range(from_pos))) for name, r in primary.items()]
    bad = [f"{name} at {p}" for name, p in pre if p is not None]
    out["G3_pre_onset"] = {"pass": not bad, "detail": "; ".join(bad)}
    if matched:
        ref = matched["base"]

        def upstream(raws):
            return {p: np.frombuffer(r, dtype=layout.dtype)[0]["emo"][:steer_index].tobytes() for p, r in raws.items()}

        want, bad = upstream(ref["raws"]), []
        for name, r in matched.items():
            if r["tokens"] != ref["tokens"]:
                bad.append(f"{name}: consumed different ids")
                continue
            p = _first_difference(want, upstream(r["raws"]))
            if p is not None:
                bad.append(f"{name} at {p}")
        out["G4_upstream_invariance"] = {"pass": not bad, "detail": "; ".join(bad)}
    delivered = []
    for name, r in primary.items():
        if name in ("base", "base_repeat", "zero"):
            continue
        a = np.frombuffer(base["raws"][from_pos], dtype=layout.dtype)[0]["emo"][steer_index]
        b = np.frombuffer(r["raws"][from_pos], dtype=layout.dtype)[0]["emo"][steer_index]
        if np.array_equal(a, b):
            delivered.append(name)
    out["G7_delivered"] = {"pass": not delivered, "detail": "; ".join(f"{n}: no change at the onset" for n in delivered)}
    return out


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _launcher_env() -> dict:
    out = subprocess.run([os.path.join(ROOT, "pyhost", "serve_nomos.sh"), "--model", "muse", "env"],
                         capture_output=True, text=True, check=True).stdout
    env = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", out, re.M))
    # The launcher's printout names no device, so the caller's wins: on a multi-GPU host (Gold) device 0 is someone
    # else's. Device 0 is the laptop's only GPU.
    env.update(SO_PATH=os.path.join(ROOT, "libnomos_kernel-muse.so"), NOMOS_KVSTORE="0",
               CUBLAS_WORKSPACE_CONFIG=":4096:8", CUDA_VISIBLE_DEVICES=os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
               STEERDIFF_ROUTE="1")
    return env


def _write(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x") as f:
        json.dump(obj, f, separators=(",", ":"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompts", default=",".join(PROMPTS))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--max-new", type=int, default=256)
    ap.add_argument("--system", default=SYSTEM, help="the system message; empty for the template's default")
    ap.add_argument("--scenarios", help="a JSON list of scenarios (tools/steerdiff_scenarios.py) instead of --prompts")
    ap.add_argument("--lexicons", choices=("default", "lies"), default="default")
    ap.add_argument("--samples", type=int, default=0, help="sampled free runs per arm (the examples' seeds and sampler)")
    ap.add_argument("--first-sample", type=int, default=0,
                    help="resume an interrupted sampled run at this sample, into a new --out; the gates read it")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--top-k", type=int, default=64)
    ap.add_argument("--cap-extended", type=int, default=6144, help="sampled runs: no answer by --max-new extends to this")
    ap.add_argument("--no-workspace", action="store_true", help="emotion readouts only (no J-lens workspace)")
    args = ap.parse_args(argv)
    if os.environ.get("STEERDIFF_ROUTE") != "1":       # the launcher's environment must be in place before host loads
        os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, **_launcher_env()})
    out = os.path.expanduser(args.out)
    os.makedirs(out)                                   # a fresh directory per run: never overwrite a capture
    from types import SimpleNamespace
    from transformers import AutoTokenizer
    import serve
    from host import KernelLM, _configured_hidden_size
    from serve_protocol import load_serve_protocol

    reg = readout.AssetRegistry()
    dirs_id = next(i for i, a in reg.assets.items() if a["kind"] == "directions")
    lens_id = next(i for i, a in reg.assets.items() if a["kind"] == "lens")
    dset = reg.assets[dirs_id]
    names = list(dset["names"])
    arms = [parse_arm(a, names) for a in args.arms.split(",")]
    if not arms or arms[0]["name"] != "base":
        raise SystemExit("the first arm must be base: it is the reference every comparison and gate reads")
    lm = KernelLM()
    if not lm.readout_supported():
        raise SystemExit("this library has no readout exports")
    D = _configured_hidden_size()
    tok = AutoTokenizer.from_pretrained(os.environ["TOK_DIR"])
    protocol = load_serve_protocol(tok, lm.model_id())
    eng = SimpleNamespace(protocol=protocol, tok=tok)
    units = reg.directions(dirs_id, [STEER_LAYER])[0]
    vectors = {a["name"]: arm_vector(a, units, names, dset["layer_sizes"][STEER_LAYER], D) for a in arms}
    ws_layers = [] if args.no_workspace else WS_LAYERS
    layout = readout.Layout(len(EMO_LAYERS), len(names), len(ws_layers), K if ws_layers else 0)
    settings = {"emotion": {"layers": EMO_LAYERS, "names": names},
                "workspace": {"layers": ws_layers, "k": K} if ws_layers else None}
    build = subprocess.run(["readelf", "-n", os.environ["SO_PATH"]], capture_output=True, text=True).stdout
    identity = {"library_sha256": _sha256(os.environ["SO_PATH"]),
                "library_build_id": (re.findall(r"Build ID: ([0-9a-f]+)", build) or [None])[0],
                "model_id": lm.model_id(), "directions": dirs_id, "lens": lens_id,
                "tokenizer_sha256": _sha256(os.path.join(os.environ["TOK_DIR"], "tokenizer.json")),
                "route": {k: os.environ.get(k) for k in ROUTE_KEYS}, "steer_layer": STEER_LAYER,
                "schedule": "fresh prefill, then one M=1 decode step per token; no DFlash, no KV store"}
    lex_ids, lex_dropped = {}, {}
    for name, words in (LIE_LEXICONS if args.lexicons == "lies" else LEXICONS).items():
        ids = {w: tok.encode(w, add_special_tokens=False) for w in words}
        lex_ids[name] = sorted({v[0] for v in ids.values() if len(v) == 1})
        lex_dropped[name] = sorted(w for w, v in ids.items() if len(v) != 1)
    if args.scenarios:                              # exact recorded ids: prompt (and prefix), onset, modes, reference
        with open(os.path.expanduser(args.scenarios)) as f:
            scenarios = json.load(f)
    else:
        scenarios = [{"key": p, "title": PROMPTS[p], "prompt_ids": build_prompt(serve, eng, PROMPTS[p], args.system or None),
                      "onset": None, "reference": None, "modes": ["free", "matched"], "max_new": args.max_new,
                      "source": None} for p in args.prompts.split(",")]
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    driver = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                            capture_output=True, text=True).stdout.strip()
    _write(os.path.join(out, "identity.json"), {
        "identity": identity, "settings": settings, "arms": arms, "prompts": {sc["key"]: sc["title"] for sc in scenarios},
        "scenarios": [{"key": sc["key"], "onset": sc["onset"], "modes": sc["modes"], "max_new": sc["max_new"],
                       "P": len(sc["prompt_ids"]), "reference_len": None if sc["reference"] is None else len(sc["reference"]),
                       "source": sc["source"]} for sc in scenarios],
        "max_new": args.max_new, "system": args.system or None, "lexicons": lex_ids,
        "sampling": sampling_record(args, scenarios),
        "lexicon_words_not_single_tokens": lex_dropped,
        "provenance": {"commit": commit, "driver": driver, "uname_r": platform.release(),
                       "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}})
    stop_ids = list(protocol.stop_ids)
    all_gates, all_repro, failed = {}, {}, False

    def run_one(sc, prompt_ids, from_pos, arm, mode, run_id, file_tag, ref=None, pick=None, extend_to=None,
                extra=None):
        """One capture; its run document goes to SCENARIO/runs/FILE_TAG.json. Returns the capture."""
        vec = vectors[arm["name"]]

        def arm_fn():
            lm.readout_config(EMO_LAYERS, dirs_id, ws_layers, lens_id, K)
            lm.lib.nomos_clear_steer(lm.h)
            if vec is not None:
                rc = lm.lib.nomos_set_steer(lm.h, STEER_LAYER, vec.ctypes.data)
                rc = rc or lm.lib.nomos_set_steer_from(lm.h, from_pos)
                if rc != 0:
                    lm.lib.nomos_clear_steer(lm.h)
                    raise RuntimeError(f"the kernel refused the steering (rc={rc})")

        steer = None if vec is None else {"layer": STEER_LAYER, "kind": arm["kind"], "s": arm["s"],
                                         "from_pos": from_pos}
        header = {"v": readout.V, "conversation": sc["key"], "turn": None, "response_id": run_id,
                  "settings": settings, "steer": steer}
        try:
            cap = capture(lm, prompt_ids, layout, header, arm_fn,
                          make_channel_of(serve, protocol, tok, prompt_ids[from_pos + 1:]), stop_ids,
                          effective_cap(sc, args.max_new), reference=ref, pick=pick, extend_to=extend_to)
        finally:
            lm.lib.nomos_clear_steer(lm.h)
        if ref is not None and cap["tokens"] != ref:
            raise SystemExit(f"{run_id}: G6 the replay did not consume its reference's ids")
        reference = None
        if mode == "matched":
            reference = (sc["source"] or {}).get("path") if sc["reference"] is not None else f"{sc['key']}.base.free"
        doc = steerdiff.run_doc(run_id=run_id, arm={"name": arm["name"], "steer": steer, **(extra or {})}, mode=mode,
                                reference=reference, prompt_ids=prompt_ids, tokens=cap["tokens"],
                                records=cap["records"], settings=settings, identity=identity)
        doc.update(saw_stop=cap["saw_stop"], summary=cap["summary"],
                   text=tok.decode(cap["tokens"], skip_special_tokens=False),
                   lexicons=steerdiff.lexicon_counts(doc, lex_ids, display_k=8))
        _write(os.path.join(out, sc["key"], "runs", f"{file_tag}.json"), doc)
        print(f"[steerdiff] {run_id}: {len(cap['tokens'])} tokens, {len(cap['records'])} records, "
              f"stop={cap['saw_stop']}", flush=True)
        return cap

    try:
        for sc in scenarios:
            pkey, prompt_ids = sc["key"], [int(t) for t in sc["prompt_ids"]]
            P = len(prompt_ids)
            # ruling P2-R8: steering starts at the last prompt token; a scenario whose prefix extends past the prompt
            # names the original prompt's last token as its onset
            from_pos = P - 1 if sc["onset"] is None else int(sc["onset"])
            free, matched = {}, {}
            if args.samples:            # sampled free runs: the examples' seeds, sampler and cap, sample-major
                if sc["modes"] != ["free"] or not sc.get("source"):
                    raise SystemExit(f"{pkey}: sampled runs need a free scenario built from a recorded episode")
                item, rec_dir, repro = sc["source"]["item"], os.path.dirname(sc["source"]["path"]), []
                by_name = {a["name"]: a for a in arms}
                for k, name in sample_schedule([a["name"] for a in arms], args.first_sample, args.samples):
                    arm, seed = by_name[name], steerdiff_sampler.seed_for("lies", item, k)
                    cap = run_one(sc, prompt_ids, from_pos, arm, "free", f"{pkey}.{arm['name']}.s{k}",
                                  f"{arm['name']}.s{k}.free",
                                  pick=steerdiff_sampler.Sampler(seed, args.temperature, args.top_p, args.top_k),
                                  extend_to=args.cap_extended, extra={"sample": k, "seed": seed})
                    if k == args.first_sample:      # G1 and G2 read the first sample
                        free[arm["name"]] = cap
                    if arm["name"] == "base":
                        r = reproduce(os.path.join(rec_dir, f"{item}-{k}.json"), k, seed, cap["tokens"])
                        repro.append(r)
                        print(f"[steerdiff] {pkey} base s{k}: reproduces the recorded episode: "
                              f"{r['identical']} (first difference {r['first_difference']})", flush=True)
                all_repro[pkey] = repro
            else:
                for mode in sc["modes"]:
                    for arm in arms:
                        if mode == "matched" and arm["name"] == "base_repeat" and "free" in sc["modes"]:
                            continue                    # G1 reads free generation when there is some
                        ref = None
                        if mode == "matched" and sc["reference"] is not None:
                            ref = [int(t) for t in sc["reference"]]
                        elif mode == "matched":
                            ref = free["base"]["tokens"][:len(free["base"]["tokens"]) - int(free["base"]["saw_stop"])]
                        cap = run_one(sc, prompt_ids, from_pos, arm, mode, f"{pkey}.{arm['name']}.{mode}",
                                      f"{arm['name']}.{mode}", ref=ref)
                        (free if mode == "free" else matched)[arm["name"]] = cap
            g = gates(free, matched, layout, P, from_pos, EMO_LAYERS.index(STEER_LAYER))
            all_gates[pkey] = g
            failed = failed or not all(v["pass"] for v in g.values())
            print(f"[steerdiff] {pkey} gates: " + ", ".join(f"{k} {'PASS' if v['pass'] else 'FAIL ' + v['detail']}"
                                                          for k, v in g.items()), flush=True)
    finally:
        lm.lib.nomos_clear_steer(lm.h)
        lm.readout_off()
    if all_repro:
        _write(os.path.join(out, "reproduction.json"), all_repro)
    _write(os.path.join(out, "gates.json"), all_gates)
    print("STEERDIFF GATES " + ("FAIL" if failed else "PASS"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
