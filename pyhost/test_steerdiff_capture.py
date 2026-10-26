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



class Report(unittest.TestCase):
    """tools/steerdiff_report.py on a synthetic capture: the gates line, the divergence column, the lexicon table and a
    matched-replay movers line, decoded through a stand-in tokenizer."""

    def test_the_report_names_gates_divergence_lexicons_and_movers(self):
        import tempfile
        import steerdiff
        import steerdiff_report as R

        class Tok:
            def decode(self, ids, skip_special_tokens=False):
                return "".join(f"<{i}>" for i in ids)

        settings = {"emotion": {"layers": [20, 24], "names": ["joyful", "sad"]}, "workspace": {"layers": [36, 48], "k": 4}}
        top_a, top_b = [[10, 5.0], [11, 4.0], [12, 3.0], [13, 2.0]], [[11, 6.0], [14, 4.5], [10, 3.5], [15, 1.0]]

        def doc(name, mode, tokens, top, joy):
            recs = [{"pos": i, "token": t, "source": "prompt", "channel": None, "missing": [],
                     "emotion": {"proj": [[0.0, 0.0], [0.0, 0.0]], "norm": [1.0, 1.0]}, "workspace": None}
                    for i, t in enumerate([1, 2])]
            recs += [{"pos": 2 + j, "token": t, "source": "generated", "channel": "answer", "missing": [],
                      "emotion": {"proj": [[0.0, 0.0], [joy, 0.0]], "norm": [1.0, 1.0]}, "workspace": {"top": [top, top]}}
                     for j, t in enumerate(tokens)]
            d = steerdiff.run_doc(run_id=f"S1.{name}.{mode}", arm={"name": name, "steer": None}, mode=mode,
                                  reference="S1.base.free" if mode == "matched" else None, prompt_ids=[1, 2],
                                  tokens=tokens, records=recs, settings=settings, identity={"x": 1})
            d.update(saw_stop=False, lexicons=steerdiff.lexicon_counts(d, {"celebration": [14]}, display_k=2))
            return d

        with tempfile.TemporaryDirectory() as out:
            ident = {"identity": {"library_sha256": "c660" * 16, "library_build_id": "b1", "directions": "d" * 64,
                                  "lens": "e" * 64, "tokenizer_sha256": "f" * 64, "steer_layer": 24, "schedule": "M=1",
                                  "route": {}},
                     "settings": settings, "prompts": {"S1": "a prompt"}, "max_new": 8, "system": "s",
                     "provenance": {}, "lexicons": {"celebration": [14]},
                     "arms": [{"name": "base", "kind": None, "s": None}, {"name": "joy_0.5", "kind": "joyful", "s": 0.5}]}
            os.makedirs(os.path.join(out, "S1", "runs"))
            for name, value in (("identity.json", ident),
                                ("gates.json", {"S1": {"G1_repeat": {"pass": True, "detail": ""}}})):
                with open(os.path.join(out, name), "w") as f:
                    json.dump(value, f)
            runs = [doc("base", "free", [5] * 8, top_a, 0.0), doc("joy_0.5", "free", [5, 5, 6] + [5] * 5, top_b, 2.0),
                    doc("base", "matched", [5] * 8, top_a, 0.0), doc("joy_0.5", "matched", [5] * 8, top_b, 2.0)]
            for d in runs:
                with open(os.path.join(out, "S1", "runs", f"{d['arm']['name']}.{d['mode']}.json"), "w") as f:
                    json.dump(d, f)
            text = R.report(out, Tok(), display_k=2)
        self.assertIn("Gates: G1_repeat **PASS**.", text)
        self.assertIn("| joy_0.5 | token 2: '<5>' -> '<6>' | False |", text)
        self.assertIn("| joy_0.5 | free | 8/8 | 8/8 |", text)
        self.assertIn("- L36 up: '<11>' +2 (8)", text)
        self.assertIn("- L36 entered the top 2: '<14>' x8", text)
        self.assertIn("- emotion L24: up joyful +2", text)    # only the capture's own emotion layers



def synthetic_capture(out):
    """A two-arm capture directory (base and joy_0.5, free and matched) like tools/steerdiff_capture.py writes."""
    import steerdiff
    settings = {"emotion": {"layers": [20, 24], "names": ["joyful", "sad"]}, "workspace": {"layers": [36, 48], "k": 4}}
    top_a, top_b = [[10, 5.0], [11, 4.0], [12, 3.0], [13, 2.0]], [[11, 6.0], [14, 4.5], [10, 3.5], [15, 1.0]]

    def doc(name, mode, tokens, top, joy):
        recs = [{"pos": i, "token": t, "source": "prompt", "channel": None, "missing": [],
                 "emotion": {"proj": [[0.0, 0.0], [0.0, 0.0]], "norm": [1.0, 1.0]}, "workspace": None}
                for i, t in enumerate([1, 2])]
        recs += [{"pos": 2 + j, "token": t, "source": "generated", "channel": "answer", "missing": [],
                  "emotion": {"proj": [[0.0, 0.0], [joy, 0.0]], "norm": [1.0, 1.0]}, "workspace": {"top": [top, top]}}
                 for j, t in enumerate(tokens)]
        d = steerdiff.run_doc(run_id=f"S1.{name}.{mode}", arm={"name": name, "steer": None}, mode=mode,
                              reference="S1.base.free" if mode == "matched" else None, prompt_ids=[1, 2],
                              tokens=tokens, records=recs, settings=settings, identity={"x": 1})
        d.update(saw_stop=False, lexicons=steerdiff.lexicon_counts(d, {"celebration": [14]}, display_k=2))
        return d

    ident = {"identity": {"library_sha256": "c660" * 16, "library_build_id": "b1", "directions": "d" * 64,
                          "lens": "e" * 64, "tokenizer_sha256": "f" * 64, "steer_layer": 24, "schedule": "M=1",
                          "route": {}},
             "settings": settings, "prompts": {"S1": "a prompt"}, "max_new": 8, "system": "s", "provenance": {},
             "lexicons": {"celebration": [14]},
             "arms": [{"name": "base", "kind": None, "s": None}, {"name": "joy_0.5", "kind": "joyful", "s": 0.5}]}
    os.makedirs(os.path.join(out, "S1", "runs"))
    for name, value in (("identity.json", ident), ("gates.json", {"S1": {"G1_repeat": {"pass": True, "detail": ""}}})):
        with open(os.path.join(out, name), "w") as f:
            json.dump(value, f)
    runs = [doc("base", "free", [5] * 8, top_a, 0.0), doc("joy_0.5", "free", [5, 5, 6] + [5] * 5, top_b, 2.0),
            doc("base", "matched", [5] * 8, top_a, 0.0), doc("joy_0.5", "matched", [5] * 8, top_b, 2.0)]
    for d in runs:
        with open(os.path.join(out, "S1", "runs", f"{d['arm']['name']}.{d['mode']}.json"), "w") as f:
            json.dump(d, f)
    return runs


class Export(unittest.TestCase):
    """tools/steerdiff_export.py: the UI bundle. Every comparison is steerdiff.compare's own document, so the view only
    renders; vocab decodes every id the view can show."""

    class Tok:
        def decode(self, ids, skip_special_tokens=False):
            return "".join(f"<{i}>" for i in ids)

    def test_the_bundle_indexes_every_comparison_and_carries_compares_own_document(self):
        import tempfile
        import steerdiff
        import steerdiff_export as X
        with tempfile.TemporaryDirectory() as out:
            runs = synthetic_capture(out)
            X.export(out, self.Tok(), display_k=2)
            with open(os.path.join(out, "ui", "index.json")) as f:
                index = json.load(f)
            self.assertEqual(index["schema"], X.SCHEMA)
            self.assertEqual([(c["prompt"], c["arm"], c["mode"]) for c in index["comparisons"]],
                             [("S1", "joy_0.5", "free"), ("S1", "joy_0.5", "matched")])
            self.assertEqual(index["gates"], {"S1": {"G1_repeat": {"pass": True, "detail": ""}}})
            with open(os.path.join(out, "ui", index["comparisons"][1]["path"])) as f:
                got = json.load(f)
        self.assertEqual(got["comparison"], json.loads(json.dumps(steerdiff.compare(runs[2], runs[3], 2))))
        self.assertEqual(index["comparisons"][0]["divergence"]["index"], 2)

    def test_vocab_decodes_every_token_and_candidate_the_view_can_show(self):
        import tempfile
        import steerdiff_export as X
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            X.export(out, self.Tok(), display_k=2)
            with open(os.path.join(out, "ui", "S1", "joy_0.5.free.json")) as f:
                got = json.load(f)
        ids = {str(i) for i in (1, 2, 5, 6, 10, 11, 14)}         # prompt, tokens, and the displayed candidates
        self.assertTrue(ids <= set(got["vocab"]), sorted(ids - set(got["vocab"])))
        self.assertEqual(got["vocab"]["14"], "<14>")
        self.assertEqual(got["text"]["steered"]["answer"], "<5><5><6><5><5><5><5><5>")



class Scenarios(unittest.TestCase):
    """Lie scenarios (Adam 10-26: joy, calm, sadness, fear, anger, steered and counter-steered, on the known lies)."""

    NAMES = ["desperate", "afraid", "angry", "joyful", "calm", "sad"]

    def test_the_emotion_aliases_and_counter_steering(self):
        p = lambda n: C.parse_arm(n, self.NAMES)            # noqa: E731
        self.assertEqual(p("fear_0.5")["kind"], "afraid")
        self.assertEqual((p("anger_-0.5")["kind"], p("anger_-0.5")["s"]), ("angry", -0.5))
        self.assertEqual(p("sadness_0.5")["kind"], "sad")
        self.assertEqual(p("joy_-0.5"), {"name": "joy_-0.5", "kind": "joyful", "s": -0.5})

    def test_a_matched_only_capture_gates_its_matched_runs(self):
        lm = FakeLM({3: 4, 4: 5, 5: 9})
        ref = [4, 5, 6]
        matched = {"base": run(lm, reference=ref), "base_repeat": run(lm, reference=ref),
                   "zero": run(lm, reference=ref, steer=0.0, from_pos=2),
                   "joy": run(lm, reference=ref, steer=0.5, from_pos=2)}
        g = C.gates({}, matched, LAYOUT, 3, 2, STEER_INDEX)
        self.assertEqual(sorted(g), ["G1_repeat", "G2_armed_zero", "G3_pre_onset", "G4_upstream_invariance",
                                     "G7_delivered"])
        self.assertTrue(all(v["pass"] for v in g.values()), g)
        matched["base_repeat"] = run(lm, reference=ref, steer=0.25, from_pos=4)
        self.assertFalse(C.gates({}, matched, LAYOUT, 3, 2, STEER_INDEX)["G1_repeat"]["pass"])

    def test_a_free_only_capture_has_no_upstream_gate(self):
        lm = FakeLM({3: 4, 4: 5, 5: 9})
        free = {"base": run(lm), "joy": run(lm, steer=0.5, from_pos=2)}
        self.assertNotIn("G4_upstream_invariance", C.gates(free, {}, LAYOUT, 3, 2, STEER_INDEX))


@unittest.skipUnless(os.path.exists("/home/adam/nomos_data/emotions/glimmer/examples/lies/none/8b241c2db1d5-2.json"),
                     "the recorded lie episodes are absent")
class LieScenarioBuilder(unittest.TestCase):
    def test_the_replay_and_answer_scenarios_split_the_recorded_ids_at_the_answer(self):
        from transformers import AutoTokenizer
        import serve
        from serve_protocol import load_serve_protocol
        import steerdiff_scenarios as S
        tok = AutoTokenizer.from_pretrained(os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer"))
        protocol = load_serve_protocol(tok, 2)
        path = "/home/adam/nomos_data/emotions/glimmer/examples/lies/none/8b241c2db1d5-2.json"
        with open(path) as f:
            ep = json.load(f)
        replay, answer = S.lie_scenarios(path, "TATHUGA", tok, protocol, serve._atem_channel, tail=256)
        kv, P = ep["kv"], 74
        self.assertEqual((replay["key"], replay["modes"], replay["onset"]), ("TATHUGA-replay", ["matched"], P - 1))
        self.assertEqual(replay["prompt_ids"] + replay["reference"], kv)          # every recorded id, nothing else
        self.assertEqual(len(replay["reference"]), 256 + 748)                     # the thinking tail and the whole answer
        self.assertEqual((answer["key"], answer["modes"], answer["onset"]), ("TATHUGA-answer", ["free"], P - 1))
        self.assertEqual(answer["prompt_ids"], kv[:P + 2135])                     # the prompt and the whole thinking
        self.assertIsNone(answer["reference"])
        self.assertEqual(replay["source"]["sha256"], answer["source"]["sha256"])
        self.assertEqual(replay["source"]["label"], "lie")

    def test_the_reason_scenario_starts_at_the_recorded_prompt_and_reasons_freely(self):
        import steerdiff_scenarios as S
        path = "/home/adam/nomos_data/emotions/glimmer/examples/lies/none/8b241c2db1d5-2.json"
        with open(path) as f:
            kv = json.load(f)["kv"]
        sc = S.reason_scenario(path, "TATHUGA", max_new=4096)
        self.assertEqual((sc["key"], sc["modes"], sc["onset"], sc["reference"]), ("TATHUGA-reason", ["free"], 73, None))
        self.assertEqual(sc["prompt_ids"], kv[:74])                        # only the recorded prompt: every arm reasons
        self.assertEqual((sc["max_new"], sc["source"]["label"], sc["source"]["prompt_len"]), (4096, "lie", 74))



class ReportMatchedOnly(unittest.TestCase):
    def test_a_matched_only_scenario_still_reports_its_replay(self):
        import tempfile
        import steerdiff_report as R
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            for arm in ("base", "joy_0.5"):
                os.remove(os.path.join(out, "S1", "runs", f"{arm}.free.json"))
            text = R.report(out, ExportTok(), display_k=2)
        self.assertNotIn("### Free generation", text)
        self.assertIn("### Matched input replay", text)
        self.assertIn("consumed the same reference ids (S1.base.free)", text)
        self.assertIn("- L36 up: '<11>' +2 (8)", text)


class ExportTok:
    def decode(self, ids, skip_special_tokens=False):
        return "".join(f"<{i}>" for i in ids)



class ReportByChannel(unittest.TestCase):
    def test_the_report_splits_lexicons_and_emotion_by_channel(self):
        import tempfile
        import steerdiff_report as R
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            d = os.path.join(out, "S1", "runs")
            for name in os.listdir(d):
                with open(os.path.join(d, name)) as f:
                    doc = json.load(f)
                for r in doc["records"]:
                    if r["source"] == "generated" and r["pos"] < 5:
                        r["channel"] = "reasoning"      # positions 2-4 think, 5-9 answer
                with open(os.path.join(d, name), "w") as f:
                    json.dump(doc, f)
            text = R.report(out, ExportTok(), display_k=2)
        self.assertIn("### Lexicons by channel", text)
        self.assertIn("| joy_0.5 | matched | reasoning | 3/3 | 3/3 |", text)
        self.assertIn("| joy_0.5 | matched | answer | 5/5 | 5/5 |", text)
        self.assertIn("- emotion L24 (answer): up joyful +2", text)
        self.assertIn("- emotion L24 (reasoning): up joyful +2", text)



class ReportSigns(unittest.TestCase):
    def test_up_lists_carry_only_rises_and_down_lists_only_falls(self):
        import tempfile
        import steerdiff_report as R
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            text = R.report(out, ExportTok(), display_k=2)
        self.assertIn("- L36 up: '<11>' +2 (8)\n", text)          # '<10>' fell, so it is not a rise
        self.assertIn("- L36 down: '<10>' -1.5 (8)\n", text)       # '<11>' rose, so it is not a fall
        self.assertIn("- emotion L24: up joyful +2; down (none)\n", text)



class ExportScenarios(unittest.TestCase):
    """Codex 10-26: the view shows provenance from the bundle, never guessed from a scenario's name."""

    def test_each_comparison_carries_its_scenario_and_the_index_keys_them_by_prompt(self):
        import tempfile
        import steerdiff_export as X
        src = {"path": "/x/episode.json", "label": "lie", "classifier": "overclaim", "answer_start": 9}
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            with open(os.path.join(out, "identity.json")) as f:
                ident = json.load(f)
            ident["scenarios"] = [{"key": "S1", "onset": 1, "modes": ["free", "matched"], "max_new": 8, "P": 2,
                                   "reference_len": None, "source": src}]
            with open(os.path.join(out, "identity.json"), "w") as f:
                json.dump(ident, f)
            X.export(out, ExportTok(), display_k=2)
            with open(os.path.join(out, "ui", "index.json")) as f:
                index = json.load(f)
            with open(os.path.join(out, "ui", "S1", "joy_0.5.matched.json")) as f:
                comp = json.load(f)
        self.assertEqual(index["scenarios"]["S1"]["source"], src)
        self.assertEqual(index["scenarios"]["S1"]["title"], "a prompt")
        self.assertEqual(comp["scenario"], index["scenarios"]["S1"])

    def test_a_capture_without_scenarios_says_so(self):
        import tempfile
        import steerdiff_export as X
        with tempfile.TemporaryDirectory() as out:
            synthetic_capture(out)
            X.export(out, ExportTok(), display_k=2)
            with open(os.path.join(out, "ui", "index.json")) as f:
                index = json.load(f)
            with open(os.path.join(out, "ui", "S1", "joy_0.5.free.json")) as f:
                comp = json.load(f)
        self.assertEqual((index["scenarios"], comp["scenario"]), ({}, None))



@unittest.skipUnless(os.path.exists("/home/adam/nomos_data/emotions/glimmer/examples/lies/none/8b241c2db1d5-2.json"),
                     "the recorded lie episodes are absent")
class ChannelPriming(unittest.TestCase):
    """A run whose prefix reaches past the original prompt labels its tokens as a parser reading the whole recorded
    generation from its start would: the replay's thinking tail is reasoning, the answer scenario's tokens are answer."""

    def test_a_primed_parser_labels_like_the_full_generation(self):
        from transformers import AutoTokenizer
        import serve
        from serve_protocol import load_serve_protocol
        import steerdiff_scenarios as S
        tok = AutoTokenizer.from_pretrained(os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer"))
        protocol = load_serve_protocol(tok, 2)
        path = "/home/adam/nomos_data/emotions/glimmer/examples/lies/none/8b241c2db1d5-2.json"
        replay, answer = S.lie_scenarios(path, "TATHUGA", tok, protocol, serve._atem_channel, tail=256)
        P = replay["onset"] + 1
        gen = replay["prompt_ids"][P:] + replay["reference"]
        truth = C.make_channel_of(serve, protocol, tok)
        want = [truth(t) for t in gen]
        cut, a0 = len(replay["prompt_ids"]) - P, len(answer["prompt_ids"]) - P
        primed = C.make_channel_of(serve, protocol, tok, replay["prompt_ids"][P:])
        self.assertEqual([primed(t) for t in replay["reference"]], want[cut:])
        self.assertEqual((want[cut], want[a0]), ("reasoning", "answer"))
        primed = C.make_channel_of(serve, protocol, tok, answer["prompt_ids"][P:])
        self.assertEqual(primed(gen[a0]), "answer")



class Relabel(unittest.TestCase):
    """tools/steerdiff_relabel.py: channels recomputed from the token sequence, primed past the original prompt."""

    def test_relabel_recomputes_channels_from_the_tokens_after_the_primer(self):
        import steerdiff_relabel as RL
        seen = {}

        def factory(primer):
            seen["primer"] = list(primer)
            state = {"answer": 99 in primer}
            def channel_of(t):
                ch = "answer" if state["answer"] else "reasoning"
                state["answer"] = state["answer"] or t == 99      # 99 plays the answer header
                return ch
            return channel_of

        doc = {"prompt_ids": [1, 2, 7, 99], "tokens": [5, 6, 3],
               "records": [{"pos": 0, "token": 1, "source": "prompt", "channel": None},
                           {"pos": 4, "token": 5, "source": "generated", "channel": "template"},
                           {"pos": 5, "token": 6, "source": "generated", "channel": "template"}]}
        new, changed = RL.relabel(doc, onset=1, factory=factory)
        self.assertEqual(seen["primer"], [7, 99])                 # every prefix token past the original prompt
        self.assertEqual([r["channel"] for r in new["records"]], [None, "answer", "answer"])
        self.assertEqual(changed, 2)
        self.assertEqual(doc["records"][1]["channel"], "template")  # the input is not mutated


if __name__ == "__main__":
    unittest.main()
