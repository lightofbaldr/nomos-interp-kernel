#!/usr/bin/env python3
"""The readout record (spec section 3), the speculative turn's record timing, and the serve's per-turn assembly of
nomos.readout/1 records (spec section 1). CPU only. Run: pixi run --no-install python3 pyhost/test_readout_records.py -v"""
import contextlib
import io
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readout  # noqa: E402

LAY = readout.Layout(2, 3, 1, 2)      # 2 emotion layers of 3 directions; 1 workspace layer with k = 2
PROMPT = [10, 11, 12, 13]
HEADER = {"v": readout.V, "conversation": "c", "turn": 0, "response_id": "chatcmpl-1", "settings": {}, "steer": None}


def recs(pos0, tokens, source, emo_valid, ws_valid=None):
    """(count, raw) as the engine would pack them: emotion values 0.5, 1.5, ... per record, workspace ids 7, scores 1.25."""
    n = len(tokens)
    emo = (np.arange(n * 2 * 4, dtype=np.float32) + 0.5).reshape(n, 2, 4)
    ids = np.full((n, 1, 2), 7, np.int32)
    sc = np.full((n, 1, 2), 1.25, np.float32)
    wv = np.asarray(ws_valid if ws_valid is not None else [0] * n, np.uint8)
    return readout.pack_records(LAY, pos0, tokens, source, emo, np.asarray(emo_valid, np.uint8), ids, sc, wv)


def turn(take=None, kv_len=None):
    return readout.ReadoutTurn(LAY, dict(HEADER), PROMPT, take=take, kv_len=kv_len)


class LM:
    """readout_records as KernelLM's: every position valid, emotion only. `log` gets each read."""

    def __init__(self):
        self.log = []

    def readout_records(self, pos0, tokens, source):
        self.log.append(("read", pos0, list(tokens), source))
        return recs(pos0, tokens, source, [1] * len(tokens))


class Records(unittest.TestCase):
    def test_the_record_size_is_the_specs(self):
        self.assertEqual(LAY.dtype.itemsize, 12 + 4 * 2 * 4 + 8 * 1 * 2)
        self.assertEqual(readout.Layout(3, 39, 4, 8).dtype.itemsize, 748)        # the default configuration
        self.assertEqual(readout.Layout(3, 39).dtype.itemsize, 492)              # emotion only

    def test_pack_keeps_only_valid_positions_in_order(self):
        count, raw = recs(5, [50, 51, 52, 53], readout.SOURCE_GENERATED, [1, 0, 1, 0], [1, 0, 0, 1])
        r = np.frombuffer(raw, LAY.dtype)
        self.assertEqual(count, 3)
        self.assertEqual(r["pos"].tolist(), [5, 7, 8])
        self.assertEqual(r["token"].tolist(), [50, 52, 53])
        self.assertEqual(r["flags"].tolist(), [3, 1, 2])
        self.assertEqual(r["source"].tolist(), [1, 1, 1])


class Emitter(unittest.TestCase):
    def test_each_record_goes_out_just_before_the_next_token(self):
        log = []

        class LM:
            def readout_records(self, pos0, tokens, source):
                log.append(("read", pos0, list(tokens), source))
                return recs(pos0, tokens, source, [1] * len(tokens))

        em = readout.SpecEmitter(LM(), PROMPT, lambda count, raw: log.append(("sink", count)))
        em.start(4)
        for tok in (100, 101, 102):
            em.handed(tok)
            log.append(("yield", tok))
        self.assertEqual(log, [("read", 0, PROMPT, 0), ("sink", 4), ("yield", 100),
                               ("read", 4, [100], 1), ("sink", 1), ("yield", 101),
                               ("read", 5, [101], 1), ("sink", 1), ("yield", 102)])

    def test_a_kv_shorter_than_the_prompt_sends_no_prompt_records(self):
        log = []

        class LM:
            def readout_records(self, pos0, tokens, source):
                log.append(("read", pos0, list(tokens), source))
                return recs(pos0, tokens, source, [1] * len(tokens))

        em = readout.SpecEmitter(LM(), PROMPT, lambda count, raw: None)
        em.start(3)                                  # a continuation that began with BOS lost it in the KV
        em.handed(100)
        em.handed(101)
        self.assertEqual(log, [("read", 3, [100], 1)])


class Turn(unittest.TestCase):
    def test_a_turn_assembles_records_with_channels_then_a_summary(self):
        t = turn()
        t.add(*recs(1, PROMPT[1:], readout.SOURCE_PROMPT, [1, 1, 1]))   # position 0 came from the KV store
        t.note_token(100, "template")
        t.add(*recs(4, [100], readout.SOURCE_GENERATED, [1], [1]))      # 100's record, before 101 is handed out
        t.note_token(101, "reasoning")
        t.add(*recs(5, [101], readout.SOURCE_GENERATED, [1], [1]))
        t.note_token(102, "answer")
        t.add(*recs(6, [102], readout.SOURCE_GENERATED, [1], [1]))
        t.note_token(200, "template")                                   # the stop token: never fed, no record
        p = t.payload()
        self.assertEqual(p["header"]["response_id"], "chatcmpl-1")
        self.assertEqual([r["pos"] for r in p["readout"]], [1, 2, 3, 4, 5, 6])
        first, gen = p["readout"][0], p["readout"][3]
        self.assertEqual((first["source"], first["channel"], first["workspace"]), ("prompt", None, None))
        self.assertEqual(first["missing"], ["workspace_prompt_v1", "channel_prompt_v1"])
        self.assertEqual((gen["source"], gen["channel"], gen["token"], gen["missing"]), ("generated", "template", 100, []))
        self.assertEqual(gen["emotion"], {"proj": [[0.5, 1.5, 2.5], [4.5, 5.5, 6.5]], "norm": [3.5, 7.5]})
        self.assertEqual(gen["workspace"], {"top": [[[7, 1.25], [7, 1.25]]]})
        self.assertIsNone(t.payload(), "nothing new since the last payload")
        s = t.summary(saw_stop=True)
        self.assertEqual(s["ranges"], {"reused": [0, 1], "prompt": [1, 4], "generated": [4, 7]})
        self.assertEqual(s["missing"], {"kv_reused": 1, "natural_stop": 1, "settle_trimmed": 0})
        self.assertEqual(s["counts"], {"records": 6, "emotion_valid": 6, "workspace_valid": 3})
        self.assertEqual([r["channel"] for r in p["readout"][3:]], ["template", "reasoning", "answer"])

    def test_a_capped_speculative_turn_counts_its_last_token_as_trimmed(self):
        t = turn()
        t.add(*recs(0, PROMPT, readout.SOURCE_PROMPT, [1] * 4))
        t.note_token(100, "answer")
        t.note_token(101, "answer")
        t.add(*recs(4, [100], readout.SOURCE_GENERATED, [1], [1]))     # 101 was the last token: the settle cut its row
        f = t.final(saw_stop=False)
        self.assertEqual([r["pos"] for r in f["readout"]], [0, 1, 2, 3, 4])
        self.assertEqual(f["summary"]["missing"], {"kv_reused": 0, "natural_stop": 0, "settle_trimmed": 1})

    def test_a_record_that_does_not_match_the_handed_out_token_fails_loudly(self):
        t = turn()
        t.note_token(100, "answer")
        with self.assertRaisesRegex(ValueError, "carries token 999"):
            t.add(*recs(4, [999], readout.SOURCE_GENERATED, [1], [1]))

    def test_records_out_of_position_order_fail_loudly(self):
        t = turn()
        t.note_token(100, "answer")
        t.note_token(101, "answer")
        with self.assertRaisesRegex(ValueError, "out of order"):
            t.add(*recs(5, [101], readout.SOURCE_GENERATED, [1], [1]))     # position 4 has not come yet
        t2 = turn()
        t2.add(*recs(2, PROMPT[2:], readout.SOURCE_PROMPT, [1, 1]))
        with self.assertRaisesRegex(ValueError, "out of order"):
            t2.add(*recs(0, PROMPT[:1], readout.SOURCE_PROMPT, [1]))

    def test_a_frame_whose_size_is_not_its_count_fails_loudly(self):
        count, raw = recs(0, PROMPT, readout.SOURCE_PROMPT, [1] * 4)
        with self.assertRaisesRegex(ValueError, "does not hold"):
            turn().add(count + 1, raw)

    def test_the_base_path_takes_the_prompt_then_each_fed_token(self):
        calls = []

        def take(pos0, tokens, generated):
            calls.append((pos0, list(tokens), generated))
            n = len(tokens)
            src = readout.SOURCE_GENERATED if generated else readout.SOURCE_PROMPT
            return recs(pos0, tokens, src, [1] * n, [1 if generated else 0] * n)

        t = turn(take, kv_len=lambda: len(PROMPT))
        t.note_token(100, "answer")        # the prefill ran: the KV holds the prompt, take it
        t.note_token(101, "answer")        # the step that produced 101 fed 100
        t.end()                            # the budget ran out: the last step fed 101
        self.assertEqual(calls, [(0, PROMPT, False), (4, [100], True), (5, [101], True)])
        s = t.summary(saw_stop=False)
        self.assertEqual(s["missing"], {"kv_reused": 0, "natural_stop": 0, "settle_trimmed": 0})
        self.assertEqual(s["ranges"]["generated"], [4, 6])

    def test_complete_holds_the_header_every_record_and_the_summary(self):
        t = turn()
        t.add(*recs(0, PROMPT, readout.SOURCE_PROMPT, [1] * 4))
        t.note_token(100, "answer")
        t.payload()                                          # what streaming sent earlier is still in the reply
        c = t.complete(saw_stop=False)
        self.assertEqual((c["header"], len(c["readout"])), (HEADER, 4))
        self.assertEqual(c["summary"]["ranges"]["prompt"], [0, 4])

    def test_the_base_path_takes_its_origin_from_the_kv(self):          # review ruling R9
        """A continuation that began with BOS lost it: the KV holds 3 rows for the 4-token prompt. No prompt records (the
        KV cannot say which token sits where), and the generated takes count from the KV's 3."""
        calls = []

        def take(pos0, tokens, generated):
            calls.append((pos0, list(tokens), generated))
            return recs(pos0, tokens, readout.SOURCE_GENERATED, [1] * len(tokens), [1] * len(tokens))

        t = turn(take, kv_len=lambda: 3)
        with contextlib.redirect_stdout(io.StringIO()):
            t.note_token(100, "answer")
        t.note_token(101, "answer")
        t.end()
        self.assertEqual(calls, [(3, [100], True), (4, [101], True)])
        s = t.summary(saw_stop=False)
        self.assertEqual(s["ranges"], {"reused": [0, 3], "prompt": [3, 3], "generated": [3, 5]})

    def test_an_origin_the_kv_cannot_hold_is_refused(self):           # review ruling R9
        with self.assertRaisesRegex(ValueError, "not 2"):
            turn(lambda *a: (0, b""), kv_len=lambda: 2).note_token(100, "answer")
        with self.assertRaisesRegex(ValueError, "KV length"):
            turn(lambda *a: (0, b""))                               # a base path turn must be able to ask the KV
        t = turn()
        t.add(*recs(0, PROMPT, readout.SOURCE_PROMPT, [1] * 4))
        t.note_token(100, "answer")
        with self.assertRaisesRegex(ValueError, "out of order"):
            t.add(*recs(3, [100], readout.SOURCE_GENERATED, [1], [1]))  # the prompt records say the KV holds 4 rows

    def test_a_turn_with_readouts_off_sends_only_its_summary(self):   # review ruling R2
        header = dict(HEADER, settings=None, steer={"layer": 24, "directions": "d" * 64, "direction": "calm",
                                                     "strength": 0.7, "from_pos": 3})
        t = readout.ReadoutTurn.off(header)
        for tok in (100, 101, 200):
            t.note_token(tok, "answer")
            self.assertIsNone(t.payload())
        t.end()
        summary = {"header": header, "ranges": {"reused": [0, 0], "prompt": [0, 0], "generated": [0, 0]},
                   "counts": {"records": 0, "emotion_valid": 0, "workspace_valid": 0},
                   "missing": {"kv_reused": 0, "natural_stop": 1, "settle_trimmed": 0, "readout_off": 1}}
        self.assertEqual(t.final(saw_stop=True), {"summary": summary})
        self.assertEqual(t.complete(saw_stop=True), {"header": header, "readout": [], "summary": summary})
        with self.assertRaisesRegex(ValueError, "readouts off"):
            t.add(*recs(4, [100], readout.SOURCE_GENERATED, [1], [1]))

    def test_a_non_finite_value_is_never_sent(self):
        count, raw = recs(0, PROMPT[:1], readout.SOURCE_PROMPT, [1])
        r = np.frombuffer(raw, LAY.dtype).copy()
        r["emo"][0, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            turn().add(1, r.tobytes())


class Composed(unittest.TestCase):
    """SpecEmitter's records fed straight into ReadoutTurn, in the order the speculative stream and the serve run them:
    handed(tok) sends the previous token's record, then the serve notes tok (review ruling R9)."""

    def run_turn(self, kv_rows, tokens, kv_len=None):
        t = turn(kv_len=kv_len)
        em = readout.SpecEmitter(LM(), PROMPT, t.add)
        with contextlib.redirect_stdout(io.StringIO()):
            em.start(kv_rows)
        for tok in tokens:
            em.handed(tok)
            t.note_token(tok, "answer")
        return t

    def test_a_kv_that_holds_the_whole_prompt(self):
        t = self.run_turn(4, [100, 101, 102])
        self.assertEqual([(r["pos"], r["source"]) for r in t.payload()["readout"]],
                         [(0, "prompt"), (1, "prompt"), (2, "prompt"), (3, "prompt"), (4, "generated"), (5, "generated")])
        self.assertEqual(t.summary(saw_stop=False)["ranges"], {"reused": [0, 0], "prompt": [0, 4], "generated": [4, 6]})

    def test_a_kv_that_lost_the_continuations_bos_assembles_from_the_kvs_positions(self):
        t = self.run_turn(3, [100, 101, 102])
        self.assertEqual([(r["pos"], r["token"], r["source"]) for r in t.payload()["readout"]],
                         [(3, 100, "generated"), (4, 101, "generated")])
        s = t.summary(saw_stop=False)
        self.assertEqual(s["ranges"], {"reused": [0, 3], "prompt": [3, 3], "generated": [3, 5]})
        self.assertEqual(s["missing"], {"kv_reused": 3, "natural_stop": 0, "settle_trimmed": 1})

    def test_a_turn_that_ends_before_its_first_generated_record_asks_the_kv(self):
        t = self.run_turn(3, [100], kv_len=lambda: 3)         # one token handed out: it never gets a record
        self.assertIsNone(t.payload())
        self.assertEqual(t.summary(saw_stop=False)["ranges"], {"reused": [0, 3], "prompt": [3, 3], "generated": [3, 3]})


if __name__ == "__main__":
    unittest.main()
