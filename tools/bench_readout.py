"""Readout spine performance (spec section 2, kernel test 6). GPU, with the serve down, on the library the last build
recorded: the plan's test command, with tools/bench_readout.py in place of tools/test_readout.py -v.
- Base decode: greedy nomos_decode_step_token, timed after the prefill.
- Speculative decode: test_readout's cycle fixture (spec_cycles), timed after its prefill (spec_prefill). It does the
  per-cycle work of pyhost/host.py's speculative loop, not a whole turn (no seed emission, stops, budget tail or
  settlement), so it measures what readouts add per cycle.
- Prompt prefill: a 4000-token prompt, chunked at 256 as the serve does, timed alone.
Exit codes:
- 1: emotion-only readouts slow decode (base or speculative) or prompt prefill by more than 5%;
- 2: the workspace slows decode by more than 25%, the spec's point for reducing its cadence or layers."""
import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_readout as R  # noqa: E402

N_DECODE, N_PROMPT, REPEATS = 192, 4000, 3


def setup(k, mode, lens, norm_w):
    k.lib.nomos_readout_disable(k.h)
    if mode == "emotion":
        assert R.configure(k) == 0
    elif mode == "workspace":
        assert R.configure(k, ws=R.WS, lens=lens, norm_w=norm_w) == 0
    if mode != "off":
        assert k.lib.nomos_readout_begin_turn(k.h) == 0


def base_rate(k, mode, ids):
    """Greedy nomos_decode_step_token (the base fast path). In workspace mode, each step's row is committed."""
    tok = int(np.argmax(k.prefill(ids)))
    t0 = time.perf_counter()
    for _ in range(N_DECODE):
        tok = k.step_token(tok)
        if mode == "workspace":
            assert k.lib.nomos_readout_workspace_commit(k.h, 0, 1) == 0
    return N_DECODE / (time.perf_counter() - t0)


def spec_rate(k, mode, ids):
    """Speculative decode throughput on the cycle fixture. The prefill runs before the timer starts (review R8): the
    prompt has its own gate (prefill_seconds), and timing it here would dilute the decode overhead."""
    seed, L = R.spec_prefill(k, ids)
    t0 = time.perf_counter()
    emitted, _ = R.spec_cycles(k, seed, L, N_DECODE, commit=(mode == "workspace"))
    return len(emitted) / (time.perf_counter() - t0)


def prefill_seconds(k, ids):
    t0 = time.perf_counter()
    k.prefill(ids)
    return time.perf_counter() - t0


def runtime():
    """Run provenance beside the library's identity: the NVIDIA driver and the OS kernel release."""
    drv = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    return {"nvidia_driver": drv, "uname_r": os.uname().release}


def main():
    k = R.kernel()             # asserts the library's identity (review R8) and fills R.LIBRARY
    k.lib.nomos_clear_steer(k.h)
    R.ensure_drafter(k)        # loaded before any timing, as in the serve, so every mode pays the same tap copies
    lens, norm_w = R.lens_assets()
    ids = R.fixture_ids()[:200]                                   # natural text, so the drafter is exercised
    long_ids = np.resize(R.PROMPT, N_PROMPT).astype(np.int32)     # chunked at 256, as the serve does
    res = {}
    for mode in ("off", "emotion", "workspace"):
        setup(k, mode, lens, norm_w)
        base_rate(k, mode, ids)                                   # warm-up
        res[mode] = {"base_tok_s": max(base_rate(k, mode, ids) for _ in range(REPEATS)),
                     "spec_decode_tok_s": max(spec_rate(k, mode, ids) for _ in range(REPEATS)),
                     "prefill_s": min(prefill_seconds(k, long_ids) for _ in range(REPEATS))}
    k.lib.nomos_readout_disable(k.h)
    off = res["off"]
    slow = {mode: {"base": 1 - res[mode]["base_tok_s"] / off["base_tok_s"],
                   "spec": 1 - res[mode]["spec_decode_tok_s"] / off["spec_decode_tok_s"],
                   "prefill": res[mode]["prefill_s"] / off["prefill_s"] - 1} for mode in ("emotion", "workspace")}
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = {"library": dict(R.LIBRARY), "commit": commit, "runtime": runtime(), "n_decode": N_DECODE,
           "n_prompt": N_PROMPT, "results": res, "slowdown": slow}
    path = os.path.expanduser(f"~/nomos_data/readout-spine/bench-{R.LIBRARY['sha256'][:16]}.json")
    with open(path + ".tmp", "w") as f:
        json.dump(out, f, indent=1)
    os.replace(path + ".tmp", path)
    print(json.dumps(out, indent=1))
    e = slow["emotion"]
    if max(e["base"], e["spec"], e["prefill"]) > 0.05:
        sys.exit(1)
    if max(slow["workspace"]["base"], slow["workspace"]["spec"]) > 0.25:
        sys.exit(2)


if __name__ == "__main__":
    main()
