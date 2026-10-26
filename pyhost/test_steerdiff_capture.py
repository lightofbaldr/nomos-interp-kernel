"""Steering diff capture (tools/steerdiff_capture.py) on CPU: the free and matched loops and the gates, against a fake
engine; the arm names; and continuity with wsdiff discovery 1 v2 (its openings and its recorded steering vectors),
skipped where those records are absent. Run: pixi run --no-install python3 pyhost/test_steerdiff_capture.py"""
import hashlib
import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import readout            # noqa: E402
import steerdiff_capture as C   # noqa: E402

LAYOUT = readout.Layout(3, 2, 2, 4)        # emotion layers [20, 24, 29]: the steering layer 24 is index 1
STEER_INDEX = 1
HEADER = {"v": readout.V, "conversation": None, "turn": None, "response_id": "t", "settings": None, "steer": None}
PROMPT = [1, 2, 3]
V2 = "/home/adam/nomos_data/emotions/glimmer/wsdiff/discovery1/run-20261024-170208"


class FakeLM:
    """KernelLM's surface for capture(). The argmax follows nxt (token -> next token). A record's emotion encodes its
    position (column 0) and the steering it saw (column 1), at the steering layer and after it, from the onset on."""

    def __init__(self, nxt):
        self.nxt, self.kv, self.fed = nxt, 0, []
        self.steer, self.from_pos, self.leak = 0.0, None, None

    def prefill(self, ids):
        self.kv, self.fed = len(ids), []
        return self._logits(ids[-1])

    def step(self, tok):
        self.fed.append(int(tok))
        self.kv += 1
        return self._logits(tok)

    def _logits(self, last):
        out = np.zeros(32, dtype=np.float32)
        out[self.nxt.get(int(last), 0)] = 1.0
        return out

    def cache_len(self):
        return self.kv

    def readout_take(self, pos0, tokens, generated):
        n = len(tokens)
        emo = np.zeros((n, LAYOUT.n_emo, LAYOUT.E + 1), dtype=np.float32)
        for i in range(n):
            p = pos0 + i
            emo[i, :, 0] = p
            onset = self.from_pos is not None and p >= self.from_pos
            if onset:
                emo[i, STEER_INDEX:, 1] += self.steer
            if self.leak == "upstream" and onset:
                emo[i, 0, 1] += 1.0
            if self.leak == "pre_onset":
                emo[i, :, 1] += 1.0
        ids = np.tile(np.arange(LAYOUT.k, dtype=np.int32), (n, LAYOUT.n_ws, 1))
        sc = np.tile(np.arange(LAYOUT.k, 0, -1).astype(np.float32), (n, LAYOUT.n_ws, 1))
        src = readout.SOURCE_GENERATED if generated else readout.SOURCE_PROMPT
        ws_ok = np.full(n, 1 if generated else 0, dtype=np.uint8)
        return readout.pack_records(LAYOUT, pos0, tokens, src, emo, np.ones(n, np.uint8), ids, sc, ws_ok)


def run(lm, max_new=10, reference=None, steer=0.0, from_pos=None, leak=None):
    def arm():
        lm.steer, lm.from_pos, lm.leak = steer, from_pos, leak
    return C.capture(lm, PROMPT, LAYOUT, HEADER, arm, lambda t: "answer", (9,), max_new, reference=reference)


def generated(cap):
    return [(r["pos"], r["token"]) for r in cap["records"] if r["source"] == "generated"]


class Loop(unittest.TestCase):
    def test_a_free_run_hands_out_its_stop_token_without_feeding_it(self):
        lm = FakeLM({3: 4, 4: 5, 5: 9})
        cap = run(lm)
        self.assertEqual((cap["tokens"], lm.fed, cap["saw_stop"]), ([4, 5, 9], [4, 5], True))
        self.assertEqual(generated(cap), [(3, 4), (4, 5)])
        self.assertEqual(cap["summary"]["missing"]["natural_stop"], 1)

    def test_a_free_run_at_its_budget_feeds_and_records_its_last_token(self):
        lm = FakeLM({3: 4, 4: 5, 5: 6, 6: 7})
        cap = run(lm, max_new=3)
        self.assertEqual((cap["tokens"], lm.fed, cap["saw_stop"]), ([4, 5, 6], [4, 5, 6], False))
        self.assertEqual(generated(cap), [(3, 4), (4, 5), (5, 6)])

    def test_a_matched_replay_feeds_exactly_its_reference(self):
        lm = FakeLM({3: 4, 4: 5, 5: 9})                         # the argmax would say 4, 5, 9
        cap = run(lm, reference=[7, 8, 7])
        self.assertEqual((cap["tokens"], lm.fed), ([7, 8, 7], [7, 8, 7]))
        self.assertEqual(generated(cap), [(3, 7), (4, 8), (5, 7)])

    def test_prompt_records_and_raw_bytes_cover_every_consumed_position(self):
        cap = run(FakeLM({3: 4, 4: 5, 5: 9}))
        self.assertEqual(sorted(cap["raws"]), [0, 1, 2, 3, 4])   # the stop token (position 5) is never fed


class Gates(unittest.TestCase):
    def setUp(self):
        self.lm = FakeLM({3: 4, 4: 5, 5: 9})
        self.free = {"base": run(self.lm), "base_repeat": run(self.lm), "zero": run(self.lm, steer=0.0, from_pos=2),
                     "joy": run(self.lm, steer=0.5, from_pos=2)}
        self.ref = self.free["base"]["tokens"][:-1]              # the consumed ids: every token but the stop
        self.matched = {"base": run(self.lm, reference=self.ref),
                        "joy": run(self.lm, reference=self.ref, steer=0.5, from_pos=2)}

    def gates(self):
        return C.gates(self.free, self.matched, LAYOUT, 3, 2, STEER_INDEX)

    def test_every_gate_passes_when_steering_moves_only_what_it_can_reach(self):
        g = self.gates()
        self.assertEqual(sorted(g), ["G1_repeat", "G2_armed_zero", "G3_pre_onset", "G4_upstream_invariance",
                                     "G7_delivered"])
        self.assertTrue(all(v["pass"] for v in g.values()), g)

    def test_a_repeat_that_differs_fails_G1_and_names_the_position(self):
        self.free["base_repeat"] = run(self.lm, steer=0.25, from_pos=4)
        g = self.gates()["G1_repeat"]
        self.assertEqual((g["pass"], g["detail"]), (False, "base_repeat: first differing record at 4"))

    def test_an_armed_zero_that_differs_fails_G2(self):
        self.free["zero"] = run(self.lm, steer=0.125, from_pos=2)
        self.assertFalse(self.gates()["G2_armed_zero"]["pass"])

    def test_steering_before_its_onset_fails_G3(self):
        self.free["joy"] = run(self.lm, steer=0.5, from_pos=2, leak="pre_onset")
        g = self.gates()["G3_pre_onset"]
        self.assertEqual((g["pass"], g["detail"]), (False, "joy at 0"))

    def test_a_change_upstream_of_the_steering_layer_fails_G4(self):
        self.matched["joy"] = run(self.lm, reference=self.ref, steer=0.5, from_pos=2, leak="upstream")
        g = self.gates()["G4_upstream_invariance"]
        self.assertEqual((g["pass"], g["detail"]), (False, "joy at 2"))

    def test_a_steered_arm_that_changes_nothing_at_its_onset_fails_G7(self):
        self.free["joy"] = run(self.lm, steer=0.0, from_pos=2)
        g = self.gates()["G7_delivered"]
        self.assertEqual((g["pass"], g["detail"]), (False, "joy: no change at the onset"))


class Arms(unittest.TestCase):
    NAMES = ["desperate", "afraid", "joyful", "calm"]

    def test_arm_names(self):
        p = lambda n: C.parse_arm(n, self.NAMES)            # noqa: E731
        self.assertEqual(p("base"), {"name": "base", "kind": None, "s": None})
        self.assertEqual(p("zero"), {"name": "zero", "kind": "zero", "s": 0.0})
        self.assertEqual(p("joy_0.3"), {"name": "joy_0.3", "kind": "joyful", "s": 0.3})
        self.assertEqual(p("rand_0.5"), {"name": "rand_0.5", "kind": "random1", "s": 0.5})
        self.assertEqual(p("calm_-0.4"), {"name": "calm_-0.4", "kind": "calm", "s": -0.4})
        self.assertEqual(p("random2_0.1")["kind"], "random2")
        for bad in ("joy", "happy_0.3", "joy_2.5", "joy_nan"):
            with self.assertRaises(ValueError, msg=bad):
                p(bad)

    def test_an_arm_without_steering_has_no_vector_and_zero_is_exactly_zero(self):
        units = np.eye(4, 6, dtype=np.float32)
        self.assertIsNone(C.arm_vector(C.parse_arm("base", self.NAMES), units, self.NAMES, 3.0, 6))
        z = C.arm_vector(C.parse_arm("zero", self.NAMES), units, self.NAMES, 3.0, 6)
        self.assertEqual((z.dtype, z.tobytes()), (np.float32, bytes(24)))
        joy = C.arm_vector(C.parse_arm("joy_0.5", self.NAMES), units, self.NAMES, 3.0, 6)
        self.assertEqual(joy.tolist(), [0.0, 0.0, 1.5, 0.0, 0.0, 0.0])


@unittest.skipUnless(os.path.exists(os.path.join(V2, "generations.jsonl")), "wsdiff v2's records are absent")
class ContinuityWithWsdiffV2(unittest.TestCase):
    def test_zero_and_random1_vectors_are_byte_identical_to_v2s_and_joy_matches_the_serves(self):
        with open(os.path.join(V2, "generations.jsonl")) as f:
            rows = [json.loads(line) for line in f]
        want = {r["arm"]: (r["v_sha256"], r["v_norm"]) for r in rows if r["prompt_id"] == "S1"}
        reg = readout.AssetRegistry()
        dirs_id = next(i for i, a in reg.assets.items() if a["kind"] == "directions")
        dset = reg.assets[dirs_id]
        units = reg.directions(dirs_id, [24])[0]
        for arm in ("zero", "rand_0.3", "rand_0.5", "rand_0.7", "joy_0.3", "joy_0.5", "joy_0.7"):
            a = C.parse_arm(arm, dset["names"])
            v = C.arm_vector(a, units, dset["names"], dset["layer_sizes"][24], 6656)
            sha = hashlib.sha256(np.ascontiguousarray(v, dtype=np.float32).tobytes()).hexdigest()
            if a["kind"] == "joyful":   # v2 normalized sp1's raw vector in float64; the serve uses the asset's float32 unit
                self.assertAlmostEqual(float(np.linalg.norm(v.astype(np.float64))), want[arm][1], delta=1e-4, msg=arm)
                self.assertTrue(np.array_equal(v, reg.steer_vector(dirs_id, 24, "joyful", a["s"])), arm)
            else:
                self.assertEqual(sha, want[arm][0], arm)

    def test_the_serves_prompt_builder_reproduces_v2s_openings(self):
        from types import SimpleNamespace
        from transformers import AutoTokenizer
        import serve
        from serve_protocol import load_serve_protocol
        tok = AutoTokenizer.from_pretrained(os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer"))
        eng = SimpleNamespace(protocol=load_serve_protocol(tok, 2), tok=tok)
        with open(os.path.join(V2, "openings.json")) as f:
            openings = json.load(f)
        for key, text in C.PROMPTS.items():
            self.assertEqual(C.build_prompt(serve, eng, text), openings[key], key)


if __name__ == "__main__":
    unittest.main()
