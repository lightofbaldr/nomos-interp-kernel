#!/usr/bin/env python3
"""The serve's `nomos` block (spec section 3): validation, steering and readouts armed inside the lock, records on the SSE
stream and in the non-streaming reply; the restart handshake (review ruling R4) and the plain output's byte-for-byte
equality with its capture from the branch point (review ruling R8). CPU only: fake engines over the Muse tokenizer.
Run: pixi run --no-install python3 pyhost/test_readout_serve.py -v"""
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import readout  # noqa: E402
import readout_plain_golden as golden  # noqa: E402

MUSE_TOK = os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer")
GOLDEN = os.path.join(HERE, "test_fixtures", "readout_plain_sse.json")
RAW = (" to=self<|message|>Recall the capital.<|eom|>"
       "<|start|>assistant to=user<|message|>Paris.<|eot|>")
DIRS, LENS = "d" * 64, "e" * 64
E3 = 3


def write_registry(root):
    reg = {"v": 1, "assets": [
        {"id": DIRS, "kind": "directions", "dir": root, "D": 8, "layers": list(range(52)), "E": E3,
         "names": ["calm", "joyful", "afraid"], "layer_sizes": [70.0] * 52, "source": "s", "source_sha256": "0" * 64},
        {"id": LENS, "kind": "lens", "dir": root, "D": 8, "layers": [36, 40, 44, 48], "source": "s",
         "source_sha256": "0" * 64}]}
    path = os.path.join(root, "registry.json")
    with open(path, "w") as f:
        json.dump(reg, f)
    return path


class FakeLM:
    """The backends' surface for the serve over a scripted reply. Its records' emotion values are the position; a cached
    prefix's prompt positions read invalid, as after a KV-store restore. strip=1 is a continuation that began with BOS and
    lost it in the KV, which then holds one row fewer than the prompt has tokens and has no prompt records (review ruling
    R9). The base path's takes come from the serve; the speculative stream pushes records through its sink, each before
    the next token, and stops at its budget. cache_len() is the KV's length: the prompt's once a source has started,
    plus each kept generated row the speculative stream reported."""

    def __init__(self, tok, raw=RAW, cached=0, strip=0):
        self.out = tok(raw, add_special_tokens=False).input_ids
        self.cached, self.strip, self.kv, self.calls, self.layout, self.kw = cached, strip, 0, [], None, None

    def readout_supported(self):
        return True

    def readout_config(self, emo_layers, directions, ws_layers, lens, k):
        self.calls.append(("config", list(emo_layers), directions, list(ws_layers), lens, k))
        self.layout = readout.Layout(len(emo_layers), E3, len(ws_layers), k)

    def readout_off(self):
        self.calls.append(("off",))

    def steer_set(self, *a):
        self.calls.append(("steer",) + tuple(a))

    def steer_clear(self):
        self.calls.append(("clear",))

    def cache_len(self):
        return self.kv

    def records(self, pos0, tokens, source):
        lay, n = self.layout, len(tokens)
        gen = source == readout.SOURCE_GENERATED
        valid = np.array([1 if (gen or pos0 + i >= self.cached) else 0 for i in range(n)], np.uint8)
        emo = np.repeat(np.arange(pos0, pos0 + n, dtype=np.float32), lay.n_emo * (lay.E + 1)).reshape(
            n, lay.n_emo, lay.E + 1)
        ids = np.full((n, max(lay.n_ws, 1), max(lay.k, 1)), 9, np.int32)
        sc = np.full(ids.shape, 0.5, np.float32)
        return readout.pack_records(lay, pos0, tokens, source, emo, valid, ids, sc, np.full(n, 1 if gen else 0, np.uint8))

    def readout_take(self, pos0, tokens, generated):
        self.calls.append(("take", pos0, len(tokens), generated))
        return self.records(pos0, tokens, readout.SOURCE_GENERATED if generated else readout.SOURCE_PROMPT)

    def generate_stream(self, prompt_ids, **kw):
        self.kw = kw
        self.kv = len(prompt_ids) - self.strip           # the prefill ran
        yield from self.out

    def generate(self, prompt_ids, **kw):
        self.kw = kw
        return list(self.out)

    def spec_generate_stream(self, prompt_ids, max_new_tokens=1024, stop_ids=(), vb=8, readout_sink=None, fresh=False):
        self.kw = {"readout_sink": readout_sink is not None, "fresh": fresh}
        P = self.kv = len(prompt_ids) - self.strip
        if readout_sink is not None and not self.strip:      # SpecEmitter.start sends prompt records only then
            readout_sink(*self.records(0, list(prompt_ids), readout.SOURCE_PROMPT))
        for j, tok in enumerate(self.out[:max_new_tokens]):
            if readout_sink is not None and j:
                readout_sink(*self.records(P + j - 1, [self.out[j - 1]], readout.SOURCE_GENERATED))
                self.kv += 1
            yield tok
            if tok in stop_ids:
                return


def fake_engine(tok, lm, spec=False):
    from serve_protocol import load_serve_protocol
    protocol = load_serve_protocol(tok, 2)
    return type("FakeEngine", (), {
        "tok": tok, "protocol": protocol, "model_name": protocol.default_model_name, "lm": lm,
        "stop": protocol.stop_ids, "lock": threading.Lock(), "spec_loaded": spec, "spec_vb": 8, "proc": None,
        "rep_penalty_default": 1.0, "kvstore": {"status": "off", "dir": None},
        "readout_ok": True, "ro_active": None, "steer_armed": False})()


class DaemonSocket:
    """A KernelClient's socket to an engine daemon that dies after the stop token (Codex's re-review F3). It plays the
    daemon's replies in order, then reads as EOF. With fail_from=k, the client's k-th send (from 0) and every later one
    fail, as against a daemon that is already gone. `sent` holds what the client sent."""

    def __init__(self, replies, fail_from=None):
        self.inbox = bytearray(b"".join(struct.pack("<I", len(r)) + r for r in replies))
        self.fail_from, self.sent, self.closed = fail_from, [], False

    def sendall(self, data):
        if self.fail_from is not None and len(self.sent) >= self.fail_from:
            raise BrokenPipeError("the engine daemon is gone")
        self.sent.append(bytes(data))

    def recv(self, k):
        out = bytes(self.inbox[:k])
        del self.inbox[:k]
        return out

    def close(self):
        self.closed = True


class HandoffLock:
    """eng.lock for a request whose successor runs the moment it unlocks (Codex's re-review F4). The release sets the
    fake engine's KV length to `then`, as the next request's prefill would."""

    def __init__(self, lm, then):
        self.lm, self.then = lm, then

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.lm.kv = self.then


NOMOS = {"readout": {"emotion": {"layers": [20, 24, 29], "directions": DIRS},
                     "workspace": {"layers": [36, 40, 44, 48], "lens": LENS, "k": 8}},
         "conversation": "conv-1", "turn": 3}


@unittest.skipUnless(os.path.isdir(MUSE_TOK), "the Muse tokenizer is not on this box")
class NomosServe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from transformers import AutoTokenizer
        import serve
        cls.tok = AutoTokenizer.from_pretrained(MUSE_TOK)
        cls.serve = serve

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NOMOS_READOUT_REGISTRY": write_registry(self.tmp.name)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def req(self, nomos=None, stream=True, **kw):
        return self.serve.ChatReq(**{"messages": [{"role": "user", "content": "What is the capital of France?"}],
                                     "temperature": 0, "max_tokens": 1200, "stream": stream, "nomos": nomos, **kw})

    def chat(self, eng, req):
        """serve.chat(req) with eng as the app's engine: the non-streaming reply."""
        previous = getattr(self.serve.app.state, "engine", None)
        self.serve.app.state.engine = eng
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return self.serve.chat(req, None)
        finally:
            if previous is None:
                del self.serve.app.state.engine
            else:
                self.serve.app.state.engine = previous

    def stream(self, eng, req):
        ids, gram, thinking = self.serve._build_inputs(req, eng)
        plan = self.serve._resolve_nomos(eng, req.nomos, len(ids))
        with contextlib.redirect_stdout(io.StringIO()):
            chunks = list(self.serve._stream(req, eng, ids, gram, thinking, plan=plan))
        return ids, [json.loads(c.removeprefix("data: ")) for c in chunks if c.startswith("data: {")]

    def test_every_invalid_field_is_a_400(self):
        from fastapi import HTTPException
        eng = fake_engine(self.tok, FakeLM(self.tok))
        ro = lambda **over: {"readout": {**NOMOS["readout"], **over}}            # noqa: E731
        emo = lambda **over: ro(emotion={**NOMOS["readout"]["emotion"], **over})  # noqa: E731
        ws = lambda **over: ro(workspace={**NOMOS["readout"]["workspace"], **over})  # noqa: E731
        st = lambda **over: {"steer": {"layer": 24, "directions": DIRS, "direction": "calm", "strength": 0.7, **over}}  # noqa: E731
        bad = ["x", {"bogus": 1}, {"readout": {"workspace": NOMOS["readout"]["workspace"]}},
               emo(extra=1), emo(directions="f" * 64), emo(directions=LENS), emo(layers=[]), emo(layers=[20, 20]),
               emo(layers=[True]), emo(layers=[60]), ws(layers=[30]), ws(lens=DIRS), ws(k=0), ws(k=17), ws(k="8"),
               ro(prompt="some"), st(direction="bored"), st(layer=60), st(strength=-2.5), st(strength=2.5),
               st(strength=float("nan")), st(strength="0.5"), st(strength=True), {"conversation": 3}, {"turn": -1}]
        for b in bad:
            with self.assertRaises(HTTPException, msg=repr(b)) as cm:
                self.serve._resolve_nomos(eng, b, 10)
            self.assertEqual(cm.exception.status_code, 400, repr(b))
        eng.readout_ok = False
        with self.assertRaises(HTTPException):
            self.serve._resolve_nomos(eng, NOMOS, 10)
        eng.readout_ok, eng.protocol = True, type("Gemma", (), {"kind": "gemma"})()
        with self.assertRaises(HTTPException):
            self.serve._resolve_nomos(eng, NOMOS, 10)

    def test_a_negative_strength_is_accepted_and_steers_away(self):
        """Signed strength (spec decision 5): -0.7 resolves as -0.7 and starts at the last prompt token like any steer."""
        eng = fake_engine(self.tok, FakeLM(self.tok))
        plan = self.serve._resolve_nomos(
            eng, {"steer": {"layer": 24, "directions": DIRS, "direction": "calm", "strength": -0.7}}, 10)
        self.assertEqual((plan.steer["strength"], plan.steer["from_pos"]), (-0.7, 9))

    def test_a_plain_request_streams_without_any_nomos_field(self):
        lm = FakeLM(self.tok)
        _, payloads = self.stream(fake_engine(self.tok, lm), self.req())
        self.assertFalse(any("nomos" in p for p in payloads))
        deltas = [p["choices"][0]["delta"] for p in payloads]
        self.assertEqual("".join(d.get("reasoning_content", "") for d in deltas), "Recall the capital.")
        self.assertEqual("".join(d.get("content", "") for d in deltas), "Paris.")
        self.assertEqual(lm.calls, [])

    def check_turn(self, eng, ids, payloads, first_prompt=0):
        n = len(eng.lm.out)                      # the reply's tokens, the stop token last
        P = len(ids)
        noms = [p["nomos"] for p in payloads if "nomos" in p]
        headers = [x["header"] for x in noms if "header" in x]
        self.assertEqual(len(headers), 1, "the header goes out once")
        self.assertEqual(headers[0]["response_id"], payloads[0]["id"])
        self.assertEqual((headers[0]["conversation"], headers[0]["turn"]), ("conv-1", 3))
        self.assertEqual(headers[0]["settings"]["emotion"]["names"], ["calm", "joyful", "afraid"])
        records = [r for x in noms for r in x.get("readout", [])]
        prompt = [r for r in records if r["source"] == "prompt"]
        gen = [r for r in records if r["source"] == "generated"]
        self.assertEqual([r["pos"] for r in prompt], list(range(first_prompt, P)))
        self.assertEqual([r["pos"] for r in gen], list(range(P, P + n - 1)), "every token but the stop has a record")
        self.assertEqual([r["token"] for r in gen], eng.lm.out[:-1])
        self.assertEqual(gen[0]["emotion"]["norm"], [float(P)] * 3)
        self.assertEqual(len(gen[0]["workspace"]["top"]), 4)
        msg, eom = eng.protocol.message_id, eng.protocol.eom_id
        body, want = None, []
        for t in eng.lm.out[:-1]:
            if body is None or t in (msg, eom):
                want.append("template")
            else:
                want.append(body)
            if t == msg:
                body = "reasoning" if "reasoning" not in want else "answer"
            elif t == eom:
                body = None
        self.assertEqual([r["channel"] for r in gen], want)
        summary = noms[-1]["summary"]
        self.assertEqual(summary["ranges"], {"reused": [0, first_prompt], "prompt": [first_prompt, P],
                                             "generated": [P, P + n - 1]})
        self.assertEqual(summary["missing"], {"kv_reused": first_prompt, "natural_stop": 1, "settle_trimmed": 0})
        self.assertEqual(payloads[-1]["choices"][0]["finish_reason"], "stop")

    def test_the_base_path_takes_the_prompt_then_each_fed_token(self):
        lm = FakeLM(self.tok)
        eng = fake_engine(self.tok, lm)
        ids, payloads = self.stream(eng, self.req(NOMOS))
        self.check_turn(eng, ids, payloads)
        takes = [c for c in lm.calls if c[0] == "take"]
        self.assertEqual(takes[0], ("take", 0, len(ids), False))
        self.assertEqual(takes[1:], [("take", len(ids) + j, 1, True) for j in range(len(lm.out) - 1)],
                         "one take per generated token but the stop, each when its successor arrived")
        self.assertEqual(lm.calls[0], ("config", [20, 24, 29], DIRS, [36, 40, 44, 48], LENS, 8))
        self.assertEqual(lm.kw.get("fresh", False), False)

    def test_the_speculative_path_takes_records_from_the_stream(self):
        lm = FakeLM(self.tok)
        eng = fake_engine(self.tok, lm, spec=True)
        ids, payloads = self.stream(eng, self.req(NOMOS))
        self.check_turn(eng, ids, payloads)
        self.assertEqual([c for c in lm.calls if c[0] == "take"], [])
        self.assertEqual(lm.kw, {"readout_sink": True, "fresh": False})

    def test_prompt_all_asks_for_a_fresh_prefill(self):
        nomos = {**NOMOS, "readout": {**NOMOS["readout"], "prompt": "all"}}
        lm = FakeLM(self.tok)
        self.stream(fake_engine(self.tok, lm), self.req(nomos))
        self.assertTrue(lm.kw["fresh"])
        lm = FakeLM(self.tok)
        self.stream(fake_engine(self.tok, lm, spec=True), self.req(nomos))
        self.assertTrue(lm.kw["fresh"])

    def test_a_restored_prefix_is_summarized_as_reused(self):
        lm = FakeLM(self.tok, cached=4)
        eng = fake_engine(self.tok, lm, spec=True)
        ids, payloads = self.stream(eng, self.req(NOMOS))
        self.check_turn(eng, ids, payloads, first_prompt=4)

    def test_steering_and_readouts_are_cleared_by_a_plain_request(self):
        lm = FakeLM(self.tok)
        eng = fake_engine(self.tok, lm)
        steer = {**NOMOS, "steer": {"layer": 24, "directions": DIRS, "direction": "calm", "strength": 0.7}}
        ids, payloads = self.stream(eng, self.req(steer))
        self.assertEqual(lm.calls[0], ("steer", 24, DIRS, "calm", 0.7, len(ids) - 1))
        header = next(p["nomos"]["header"] for p in payloads if "nomos" in p and "header" in p["nomos"])
        self.assertEqual(header["steer"], {"layer": 24, "directions": DIRS, "direction": "calm", "strength": 0.7,
                                           "from_pos": len(ids) - 1})
        self.assertTrue(eng.steer_armed)
        lm.calls.clear()
        _, payloads = self.stream(eng, self.req())
        self.assertEqual(lm.calls, [("clear",), ("off",)])
        self.assertFalse(any("nomos" in p for p in payloads))
        self.assertFalse(eng.steer_armed)
        self.assertIsNone(eng.ro_active)

    def test_a_non_streaming_reply_carries_the_header_every_record_and_the_summary(self):
        lm = FakeLM(self.tok)
        eng = fake_engine(self.tok, lm)
        previous = getattr(self.serve.app.state, "engine", None)
        self.serve.app.state.engine = eng
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                resp = self.serve.chat(self.req(NOMOS, stream=False), None)
        finally:
            if previous is None:
                del self.serve.app.state.engine
            else:
                self.serve.app.state.engine = previous
        nomos = resp["nomos"]
        self.assertEqual(nomos["header"]["response_id"], resp["id"])
        self.assertEqual(resp["choices"][0]["message"]["content"], "Paris.")
        self.assertEqual(nomos["summary"]["missing"]["natural_stop"], 1)
        self.assertEqual(sum(r["source"] == "generated" for r in nomos["readout"]), len(lm.out) - 1)

    def test_a_conversation_that_is_not_a_string_is_a_400(self):      # review ruling R1: Plan 3's fake answers the same
        from fastapi.testclient import TestClient
        previous = getattr(self.serve.app.state, "engine", None)
        self.serve.app.state.engine = fake_engine(self.tok, FakeLM(self.tok))
        try:
            for stream in (True, False):
                r = TestClient(self.serve.app).post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "What is the capital of France?"}], "stream": stream,
                    "nomos": {**NOMOS, "conversation": 17}})
                self.assertEqual((r.status_code, r.headers["content-type"], r.content),
                                 (400, "application/json",
                                  b'{"detail":"nomos: conversation must be a string of at most 256 characters"}'))
        finally:
            if previous is None:
                del self.serve.app.state.engine
            else:
                self.serve.app.state.engine = previous

    def test_a_request_with_readouts_off_still_gets_its_summary(self):  # review ruling R2
        steer = {"layer": 24, "directions": DIRS, "direction": "calm", "strength": 0.7}
        for nomos in ({"steer": steer, "conversation": "17", "turn": 4}, {"conversation": "17", "turn": 4}):
            with self.subTest(steering="steer" in nomos):
                lm = FakeLM(self.tok)
                eng = fake_engine(self.tok, lm, spec=True)
                ids, payloads = self.stream(eng, self.req(nomos))
                header = {"v": readout.V, "conversation": "17", "turn": 4, "response_id": payloads[0]["id"],
                          "settings": None, "steer": dict(steer, from_pos=len(ids) - 1) if "steer" in nomos else None}
                summary = {"header": header, "ranges": {"reused": [0, 0], "prompt": [0, 0], "generated": [0, 0]},
                           "counts": {"records": 0, "emotion_valid": 0, "workspace_valid": 0},
                           "missing": {"kv_reused": 0, "natural_stop": 1, "settle_trimmed": 0, "readout_off": 1}}
                self.assertEqual([p["nomos"] for p in payloads if "nomos" in p], [{"summary": summary}],
                                 "only the finish chunk carries nomos, and it holds only the summary")
                self.assertEqual(payloads[-1]["choices"][0]["finish_reason"], "stop")
                self.assertEqual(lm.kw, {"readout_sink": False, "fresh": False}, "the plain speculative source")
                self.assertEqual([c for c in lm.calls if c[0] in ("config", "take")], [])
                resp = self.chat(eng, self.req(nomos, stream=False))
                h = dict(header, response_id=resp["id"])
                self.assertEqual(resp["nomos"], {"header": h, "readout": [], "summary": dict(summary, header=h)})

    def test_a_kv_that_lost_a_bos_is_assembled_from_the_kvs_positions(self):    # review ruling R9
        for spec in (False, True):
            with self.subTest(speculative=spec):
                lm = FakeLM(self.tok, strip=1)
                ids, payloads = self.stream(fake_engine(self.tok, lm, spec=spec), self.req(NOMOS))
                P, n = len(ids) - 1, len(lm.out)
                noms = [p["nomos"] for p in payloads if "nomos" in p]
                records = [r for x in noms for r in x.get("readout", [])]
                self.assertEqual([(r["pos"], r["token"], r["source"]) for r in records],
                                 [(P + j, t, "generated") for j, t in enumerate(lm.out[:-1])])
                self.assertEqual(noms[-1]["summary"]["ranges"],
                                 {"reused": [0, P], "prompt": [P, P], "generated": [P, P + n - 1]})

    def after_stop(self, nomos, end):
        """A speculative turn through the real KernelClient over a DaemonSocket (Codex's re-review F3). The daemon streams
        the reply through its stop token, then ends with `end`: "ok" sends its end frame, "eof" closes, and "next" makes the
        client's request for what follows the stop token fail. Returns (the SSE chunks, the error the stream raised, the
        client)."""
        from test_spec_pull import bare_client
        fake = FakeLM(self.tok)                          # builds the daemon's frames: the reply and its records
        eng = fake_engine(self.tok, None, spec=True)
        req = self.req(nomos)
        ids, gram, thinking = self.serve._build_inputs(req, eng)

        def frame(count, raw):
            return struct.pack("<ii", 2, count) + raw

        replies = []
        if nomos is not None:
            replies.append(struct.pack("<i", 0))         # OP_READOUT_CONFIG's reply
            fake.readout_config([20, 24, 29], DIRS, [36, 40, 44, 48], LENS, 8)
            replies.append(frame(*fake.records(0, ids, readout.SOURCE_PROMPT)))
        for j, tok in enumerate(fake.out):
            if nomos is not None and j:
                replies.append(frame(*fake.records(len(ids) + j - 1, [fake.out[j - 1]], readout.SOURCE_GENERATED)))
            replies.append(struct.pack("<ii", 1, tok))
        if end == "ok":
            replies.append(struct.pack("<i", 0))         # the daemon's end frame
        # The client's sends before its request for what follows the stop token: the configuration (with nomos), the
        # stream request, and a next-token request after each token but the stop.
        before = (1 if nomos is not None else 0) + len(fake.out)
        eng.lm = client = bare_client(DaemonSocket(replies, fail_from=before if end == "next" else None))
        plan = self.serve._resolve_nomos(eng, nomos, len(ids))
        chunks, error = [], None
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                self.serve, "time", SimpleNamespace(time=lambda: 1767225600.25, monotonic=time.monotonic)):
            try:
                for chunk in self.serve._stream(req, eng, ids, gram, thinking, plan=plan):
                    chunks.append(chunk)
            except Exception as e:  # noqa: BLE001
                error = e
        return chunks, error, client

    def check_after_stop(self, end, kind, message):
        """Review ruling A3: a nomos stream whose daemon fails after the stop token raises that failure and ends without
        a summary or [DONE]. A plain stream keeps its old bytes: its reply is complete by then."""
        ok, error, client = self.after_stop(NOMOS, "ok")
        self.assertIsNone(error)
        self.assertEqual(ok[-1], "data: [DONE]\n\n")
        self.assertIn('"summary"', ok[-2])
        self.assertFalse(client._dirty)
        chunks, error, client = self.after_stop(NOMOS, end)
        self.assertIsInstance(error, kind)
        self.assertEqual(str(error), message)
        self.assertTrue(any('"readout"' in c for c in chunks), "the turn's records were already streaming")
        self.assertFalse(any('"summary"' in c for c in chunks), "no summary certifies a turn whose source failed")
        self.assertNotIn("data: [DONE]\n\n", chunks)
        self.assertTrue(client._dirty and client.sock.closed)
        plain, error, client = self.after_stop(None, end)
        self.assertIsNone(error)
        self.assertEqual(plain, self.after_stop(None, "ok")[0])
        self.assertTrue(client._dirty)

    def test_a_daemon_lost_after_the_stop_token_ends_a_nomos_stream_without_a_summary(self):      # re-review F3
        self.check_after_stop("eof", RuntimeError, "engine daemon closed the spec stream")

    def test_a_failed_next_request_after_the_stop_token_ends_a_nomos_stream_without_a_summary(self):      # re-review F3
        self.check_after_stop("next", BrokenPipeError, "the engine daemon is gone")

    def test_a_non_streaming_turn_reads_its_origin_before_it_unlocks(self):      # Codex's re-review F4
        """A stripped-BOS speculative turn that hands out one token has no records, so its summary reads its origin from
        the KV. The next request may change the KV as soon as this one unlocks: the reply's summary is built before."""
        lm = FakeLM(self.tok, strip=1)
        eng = fake_engine(self.tok, lm, spec=True)
        P = len(self.serve._build_inputs(self.req(NOMOS), eng)[0]) - 1
        for then in (P + 1, P + 3):                      # the next request's KV: a plausible origin, an impossible one
            with self.subTest(next_kv=then):
                eng.lock = HandoffLock(lm, then)
                resp = self.chat(eng, self.req(NOMOS, stream=False, max_tokens=1))
                self.assertEqual(resp["nomos"]["readout"], [])
                self.assertEqual(resp["nomos"]["summary"]["ranges"],
                                 {"reused": [0, P], "prompt": [P, P], "generated": [P, P]})

    def test_health_reports_readouts(self):
        eng = fake_engine(self.tok, FakeLM(self.tok))
        previous = getattr(self.serve.app.state, "engine", None)
        self.serve.app.state.engine = eng
        try:
            h = self.serve.health()
        finally:
            if previous is None:
                del self.serve.app.state.engine
            else:
                self.serve.app.state.engine = previous
        self.assertEqual(h["readout"], {"supported": True, "assets": sorted([DIRS, LENS]), "active": None,
                                        "steering": False})


@unittest.skipUnless(os.path.isdir(MUSE_TOK), "the Muse tokenizer is not on this box")
class Restart(unittest.TestCase):
    """Review ruling R4: the engine daemon outlives a serve, and so does the steering an earlier serve armed on it. The
    real Engine, KernelClient and engine daemon handler, over golden.DaemonLM, which keeps the kernel's steering rules."""

    def test_a_restarted_serve_resets_the_daemon_before_its_first_plain_request(self):
        replies, dlm, (calls, forwards) = golden.restart_case(new=True)
        self.assertIn("steer_set", dlm.calls[:calls], "the first serve steered the daemon")
        self.assertTrue(any(steered for _, steered in dlm.forwards[:forwards]))
        self.assertEqual(dlm.calls[calls:calls + 3], ["readout_off", "steer_clear", "prepare"],
                         "the restarted serve resets the daemon before its first request")
        self.assertEqual(dlm.forwards[forwards][0], "prefill", "the plain request reused none of the steered KV")
        self.assertFalse(any(steered for _, steered in dlm.forwards[forwards:]), "and ran unsteered")
        payloads = [json.loads(line[6:]) for line in replies["stream"]["body"].splitlines() if line.startswith("data: {")]
        self.assertEqual("".join(p["choices"][0]["delta"].get("content", "") for p in payloads), "Paris.")
        self.assertFalse(any("nomos" in p for p in payloads))


@unittest.skipUnless(os.path.isdir(MUSE_TOK), "the Muse tokenizer is not on this box")
class PlainOutput(unittest.TestCase):
    """Review ruling R8: a request without a nomos block gets, byte for byte, the reply it got before Plan 2. The fixture
    was captured from the branch point's pyhost (Task 5, Step 2) and is never regenerated from changed code."""

    def test_every_plain_reply_is_byte_identical_to_its_capture_from_the_branch_point(self):
        with open(GOLDEN) as f:
            want = json.load(f)["cases"]
        got = golden.render(new=True)
        self.assertEqual(sorted(got), sorted(want))
        for name in sorted(want):
            for kind in sorted(want[name]):
                with self.subTest(case=name, reply=kind):
                    self.assertEqual(got[name][kind], want[name][kind])


if __name__ == "__main__":
    unittest.main()
