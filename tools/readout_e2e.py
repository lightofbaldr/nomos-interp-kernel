"""Readout spine end to end on the GPU (spec "Build order" step 6, without Pi). This worktree's engine daemon runs as a
subprocess and the serve runs in this process (fastapi TestClient, daemon mode); real requests carry a `nomos` block.
The serve is restarted once against the same daemon (review ruling R4).
The serve must be down; from the worktree root:
  /home/adam/Projects/glimmer-emotions/scripts/with_serve_down.sh /home/adam/nomos_data/readout-spine/logs/e2e -- \
      pixi run --no-install python3 tools/readout_e2e.py
It uses its own socket and KV store under ~/nomos_data/readout-spine/e2e, never the production ones."""
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nomos_data/readout-spine/e2e")
CHANNELS = {"template", "reasoning", "answer", "tool"}


def launcher_env():
    """The Muse launcher's environment (serve_nomos.sh env prints it and starts nothing), aimed at this worktree's
    kernel, with its own socket and store."""
    out = subprocess.run([os.path.join(ROOT, "pyhost", "serve_nomos.sh"), "--model", "muse", "env"],
                         capture_output=True, text=True, check=True).stdout
    env = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", out, re.M))
    env.update(SO_PATH=os.path.join(ROOT, "libnomos_kernel-muse.so"),
               NOMOS_ENGINE_SOCKET=os.path.join(WORK, "engine.sock"),
               NOMOS_KVSTORE_DIR=os.path.join(WORK, "kvstore"),
               CUBLAS_WORKSPACE_CONFIG=":4096:8", CUDA_VISIBLE_DEVICES="0")
    return env


def sse(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: {")]


def check_stream(payloads, tok, n_prompt_expected_first, label, max_tokens):
    noms = [p["nomos"] for p in payloads if "nomos" in p]
    headers = [x["header"] for x in noms if "header" in x]
    assert len(headers) == 1, f"{label}: {len(headers)} headers"
    assert headers[0]["response_id"] == payloads[0]["id"], label
    records = [r for x in noms for r in x.get("readout", [])]
    summary = noms[-1]["summary"]
    a, P = summary["ranges"]["prompt"]
    b, c = summary["ranges"]["generated"]
    prompt = [r for r in records if r["source"] == "prompt"]
    gen = [r for r in records if r["source"] == "generated"]
    assert [r["pos"] for r in prompt] == list(range(a, P)), f"{label}: prompt positions"
    assert [r["pos"] for r in gen] == list(range(b, c)) and b == P, f"{label}: generated positions"
    assert all(r["channel"] in CHANNELS for r in gen), f"{label}: channels"
    assert all(len(r["emotion"]["proj"]) == 3 and len(r["emotion"]["proj"][0]) == 39 for r in records), label
    if headers[0]["settings"]["workspace"]:
        assert all(r["workspace"] and len(r["workspace"]["top"]) == 4 for r in gen), f"{label}: workspace"
    if n_prompt_expected_first is not None:
        assert a == n_prompt_expected_first, f"{label}: prompt records start at {a}, expected {n_prompt_expected_first}"
    deltas = [p["choices"][0]["delta"] for p in payloads]
    finish = payloads[-1]["choices"][0]["finish_reason"]
    if finish == "stop":
        trimmed = summary["missing"]["settle_trimmed"]
        handed = (c - b) + 1 + trimmed                 # every token handed out: the recorded ones, the stop, the trimmed
        assert summary["missing"]["natural_stop"] == 1, label
        # A stop token handed out by ledger R54's exact step (the budget's last token) leaves its predecessor trimmed:
        # that step's row is dropped, so its record never goes out (ruling P2-R4). No other stop trims anything.
        assert trimmed == 0 or (trimmed == 1 and handed == max_tokens), f"{label}: settle_trimmed {trimmed}, {handed} tokens"
        for channel, field in (("reasoning", "reasoning_content"), ("answer", "content")):
            text = tok.decode([r["token"] for r in gen if r["channel"] == channel], skip_special_tokens=True)
            streamed = "".join(d.get(field, "") for d in deltas)
            if trimmed:                                # the trimmed token was streamed but has no record
                assert streamed.startswith(text.rstrip("\ufffd")), f"{label}: {channel} records {text[:60]!r}"
            else:
                assert text == streamed, f"{label}: {channel} records decode to {text[:60]!r}, streamed {streamed[:60]!r}"
    return {"prompt": [a, P], "generated": [b, c], "records": len(records), "finish": finish,
            "missing": summary["missing"]}


def main():
    shutil.rmtree(os.path.join(WORK, "kvstore"), ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    env = launcher_env()
    log = open(os.path.join(WORK, "daemon.log"), "w")
    daemon = subprocess.Popen(["pixi", "run", "--no-install", "python3", "pyhost/engine_daemon.py"], cwd=ROOT,
                              env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 900
        while "[daemon] READY" not in open(os.path.join(WORK, "daemon.log")).read():
            if daemon.poll() is not None or time.time() > deadline:
                raise SystemExit("the daemon did not come up: see " + os.path.join(WORK, "daemon.log"))
            time.sleep(2)
        os.environ.update(env)
        sys.path.insert(0, os.path.join(ROOT, "pyhost"))
        import readout
        import serve
        from fastapi.testclient import TestClient
        reg = readout.AssetRegistry()
        dirs = next(i for i, a in reg.assets.items() if a["kind"] == "directions")
        lens = next(i for i, a in reg.assets.items() if a["kind"] == "lens")
        full = {"readout": {"emotion": {"layers": [20, 24, 29], "directions": dirs},
                            "workspace": {"layers": [36, 40, 44, 48], "lens": lens, "k": 8}},
                "conversation": "e2e", "turn": 0}
        result = {}
        with TestClient(serve.app) as client:
            tok = serve.app.state.engine.tok
            assert client.get("/health").json()["readout"]["supported"], "the engine reports no readouts"
            msgs = [{"role": "user", "content": "In two sentences: what is the capital of Portugal, and what is it known for?"}]
            nomos = {**full, "readout": {**full["readout"], "prompt": "all"}}
            p1 = sse(client.post("/v1/chat/completions",
                                 json={"messages": msgs, "max_tokens": 400, "stream": True, "nomos": nomos}))
            result["turn1_all"] = check_stream(p1, tok, 0, "turn 1 (all)", 400)
            d1 = [p["choices"][0]["delta"] for p in p1]
            msgs2 = msgs + [{"role": "assistant", "content": "".join(d.get("content", "") for d in d1),
                             "reasoning_content": "".join(d.get("reasoning_content", "") for d in d1)},
                            {"role": "user", "content": "And its population?"}]
            p2 = sse(client.post("/v1/chat/completions", json={"messages": msgs2, "max_tokens": 400, "stream": True,
                                                               "nomos": {**full, "turn": 1}}))
            result["turn2_new"] = check_stream(p2, tok, None, "turn 2 (new)", 400)
            assert result["turn2_new"]["prompt"][0] > 0, "turn 2 reused nothing from the KV store"
            steer = {"readout": {"emotion": {"layers": [20, 24, 29], "directions": dirs}},
                     "steer": {"layer": 24, "directions": dirs, "direction": "calm", "strength": 0.7}}
            r3 = client.post("/v1/chat/completions", json={"messages": msgs2, "max_tokens": 200, "stream": False,
                                                           "nomos": steer}).json()
            assert r3["nomos"]["header"]["steer"]["direction"] == "calm", "turn 3 lost its steering"
            assert r3["nomos"]["summary"]["missing"]["kv_reused"] == 0, "a steered turn must bypass the KV store (R17)"
            result["turn3_steered"] = r3["nomos"]["summary"]["ranges"]
            plain = {"messages": msgs, "max_tokens": 64, "stream": True}
            p4 = sse(client.post("/v1/chat/completions", json=plain))
            assert not any("nomos" in p for p in p4), "a plain request carried readouts"
            h = client.get("/health").json()["readout"]
            assert h["active"] is None and h["steering"] is False, f"a plain request left {h} armed"
            result["health_after_plain"] = h
            # Review ruling R2: steering with readouts off still ends with the turn's summary, which says readout_off.
            p5 = sse(client.post("/v1/chat/completions", json={**plain, "nomos": {
                "steer": {"layer": 24, "directions": dirs, "direction": "calm", "strength": 0.7},
                "conversation": "e2e", "turn": 4}}))
            noms = [p["nomos"] for p in p5 if "nomos" in p]
            assert len(noms) == 1 and set(noms[0]) == {"summary"}, f"turn 5: {len(noms)} nomos chunks"
            s5 = noms[0]["summary"]
            assert s5["missing"]["readout_off"] == 1 and s5["counts"]["records"] == 0, f"turn 5: {s5}"
            assert s5["header"]["steer"]["direction"] == "calm" and s5["header"]["settings"] is None, f"turn 5: {s5}"
            result["turn5_steer_only"] = s5
        # Review ruling R4: restart the serve against the same daemon, which turn 5 left steered. The restarted serve
        # must reset it, so the same plain request gets p4's reply again: p4 and p6 both follow a steered turn, and both
        # continue from the same stored prefix, so their prefills and their greedy tokens are identical.
        with TestClient(serve.app) as client:
            assert client.get("/health").json()["readout"]["steering"] is False, "the restarted serve reports steering"
            p6 = sse(client.post("/v1/chat/completions", json=plain))
            replies = [[(p["choices"][0]["delta"], p["choices"][0]["finish_reason"]) for p in ps] for ps in (p4, p6)]
            assert replies[0] == replies[1], "after a serve restart the plain reply changed: the daemon stayed steered"
            result["restart_plain_unchanged"] = True
        with open(os.path.join(WORK, "result.json"), "w") as f:
            json.dump(result, f, indent=1)
        print(json.dumps(result, indent=1))
        print("E2E OK")
    finally:
        daemon.terminate()
        try:
            daemon.wait(60)
        except subprocess.TimeoutExpired:
            daemon.kill()


if __name__ == "__main__":
    main()
