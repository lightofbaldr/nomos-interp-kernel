#!/usr/bin/env python3
"""KernelLM's readout and steering calls and the speculative loop's record timing (spec section 3), over a toy kernel that
simulates the readout tables. CPU only. Run: pixi run --no-install python3 pyhost/test_readout_host.py -v"""
import contextlib
import ctypes
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import host  # noqa: E402
import readout  # noqa: E402
from test_kv_session_host import FakeBackend, IDS, ToyKernel, chain, ints, nxt, toy_lm  # noqa: E402
from test_readout_assets import D as AD, build_assets, ids_by_kind  # noqa: E402

LAY = readout.Layout(1, 2, 1, 2)
P = len(IDS)


class ToyReadoutKernel(ToyKernel):
    """ToyKernel plus the readout and steering exports, over simulated tables. Like the kernel (Plan 1):
    - every forward writes its rows' emotion entries;
    - a forward's staged rows are its decode row (a prefill's last token, a step) or all of a verify's rows;
    - a workspace commit makes staged rows valid;
    - begin_turn clears validity;
    - a read reports positions at or past the KV length invalid.
    A read's values are the position itself."""

    def __init__(self, width=AD, **kw):
        super().__init__(**kw)
        self.width, self.configure_rc = width, 0
        self.emo_ok, self.ws_ok, self.staged = set(), set(), []

    def _wrote(self, positions, staged):
        self.emo_ok.update(positions)
        self.staged = list(staged)

    def nomos_prefill(self, h, ids_ptr, n, out):
        rc = super().nomos_prefill(h, ids_ptr, n, out)
        self._wrote(range(0, self.kv), [self.kv - 1])
        return rc

    def nomos_prefill_cont(self, h, ids_ptr, n, out):
        before = self.kv
        rc = super().nomos_prefill_cont(h, ids_ptr, n, out)
        self._wrote(range(before, self.kv), [self.kv - 1])
        return rc

    def nomos_decode_step(self, h, tok, out):
        before = self.kv
        rc = super().nomos_decode_step(h, tok, out)
        if rc == 0:
            self._wrote([before], [before])
        return rc

    def nomos_dflash_verify_fused(self, h, rows_ptr, m, L, out_ptr):
        rc = super().nomos_dflash_verify_fused(h, rows_ptr, m, L, out_ptr)
        self._wrote(range(L, L + m), range(L, L + m))
        return rc

    def nomos_readout_configure(self, h, emo_ptr, n_emo, dirs_ptr, E, ws_ptr, n_ws, lens_ptr, norm_ptr, k):
        dirs = np.array((ctypes.c_float * (n_emo * E * self.width)).from_address(dirs_ptr)).reshape(n_emo, E, self.width)
        self.log.append(("configure", ints(emo_ptr, n_emo), E, ints(ws_ptr, n_ws) if n_ws else [], k, dirs,
                         lens_ptr != 0, norm_ptr != 0))
        return self.configure_rc

    def nomos_readout_disable(self, h):
        self.log.append(("disable",))
        return 0

    def nomos_readout_begin_turn(self, h):
        self.log.append(("begin_turn",))
        self.emo_ok.clear()
        self.ws_ok.clear()
        return 0

    def nomos_readout_workspace_commit(self, h, row0, nrows):
        self.log.append(("commit_ws", row0, nrows))
        if row0 < 0 or row0 + nrows > len(self.staged):
            return -1
        self.ws_ok.update(self.staged[row0:row0 + nrows])
        return 0

    def nomos_readout_read(self, h, pos0, n, emo, ev, ids, sc, wv):
        self.log.append(("read", pos0, n))
        width = LAY.n_emo * (LAY.E + 1)
        emo_a = np.ctypeslib.as_array((ctypes.c_float * (n * width)).from_address(emo))
        ev_a = np.ctypeslib.as_array((ctypes.c_uint8 * n).from_address(ev))
        for i in range(n):
            p = pos0 + i
            emo_a[i * width:(i + 1) * width] = p
            ev_a[i] = 1 if (p < self.kv and p in self.emo_ok) else 0
            if ids:
                kk = LAY.n_ws * LAY.k
                np.ctypeslib.as_array((ctypes.c_int32 * (n * kk)).from_address(ids))[i * kk:(i + 1) * kk] = p
                np.ctypeslib.as_array((ctypes.c_float * (n * kk)).from_address(sc))[i * kk:(i + 1) * kk] = p
                np.ctypeslib.as_array((ctypes.c_uint8 * n).from_address(wv))[i] = 1 if (p < self.kv and p in self.ws_ok) else 0
        return 0

    def nomos_set_steer(self, h, layer, vec_ptr):
        self.log.append(("set_steer", layer, np.array((ctypes.c_float * self.width).from_address(vec_ptr))))
        return 0

    def nomos_set_steer_from(self, h, pos):
        self.log.append(("set_steer_from", pos))
        return 0

    def nomos_clear_steer(self, h):
        self.log.append(("clear_steer",))
        return 0


def ro_lm(kernel, store=True):
    lm = toy_lm(kernel, store)
    lm._readout_ok, lm._ro = True, (("toy",), LAY)
    return lm


def spec(kernel, n, vb=4, stop=(), ids=IDS, fresh=False, store=True):
    """One speculative turn with a record sink: (tokens, records as (pos, token, flags, source), the event order)."""
    events, records = [], []

    def sink(count, raw):
        r = np.frombuffer(raw, LAY.dtype)
        assert len(r) == count
        for x in r:
            records.append((int(x["pos"]), int(x["token"]), int(x["flags"]), int(x["source"])))
            events.append(("rec", int(x["pos"])))

    toks = []
    with contextlib.redirect_stdout(io.StringIO()):
        for t in ro_lm(kernel, store).spec_generate_stream(ids, max_new_tokens=n, stop_ids=stop, vb=vb,
                                                           readout_sink=sink, fresh=fresh):
            events.append(("tok", t))
            toks.append(t)
    return toks, records, events


GEN = readout.SOURCE_GENERATED
BOTH = readout.FLAG_EMO | readout.FLAG_WS


class SpecRecords(unittest.TestCase):
    def test_records_cover_the_prompt_and_every_kept_token_but_the_last(self):
        k = ToyReadoutKernel(script=[3, 0, 2])
        toks, records, _ = spec(k, 8)
        self.assertEqual(toks, chain(IDS[-1], 8))
        prompt = [(p, IDS[p], readout.FLAG_EMO, readout.SOURCE_PROMPT) for p in range(P)]
        gen = [(P + j, toks[j], BOTH, GEN) for j in range(7)]
        self.assertEqual(records, prompt + gen)

    def test_a_tokens_record_comes_just_before_the_next_token(self):
        toks, _, events = spec(ToyReadoutKernel(script=[3, 0, 2]), 8)
        expect = [("tok", toks[0])]
        for j in range(1, 8):
            expect += [("rec", P + j - 1), ("tok", toks[j])]
        self.assertEqual(events[P:], expect)

    def test_a_stop_inside_a_burst_leaves_it_and_the_rest_of_the_burst_without_records(self):
        k = ToyReadoutKernel(script=[3])
        want = chain(IDS[-1], 8)
        toks, records, _ = spec(k, 8, stop=(want[2],))
        self.assertEqual(toks, want[:3])
        self.assertEqual([r[0] for r in records if r[3] == GEN], [P, P + 1])
        self.assertEqual(k.kv, P + 2, "the settle cut the stop token's row and the drafts after it")

    def test_an_exact_step_at_the_budget_leaves_its_two_tokens_without_records(self):     # ledger R54
        toks, records, _ = spec(ToyReadoutKernel(script=[0]), 2)
        self.assertEqual(len(toks), 2)
        self.assertEqual([r for r in records if r[3] == GEN], [])

    def test_a_cached_prefix_has_no_prompt_records_and_fresh_recomputes_it(self):
        _, records, _ = spec(ToyReadoutKernel(cached=4, script=[3]), 3)
        self.assertEqual([r[0] for r in records if r[3] == readout.SOURCE_PROMPT], list(range(4, P)))
        k = ToyReadoutKernel(cached=4, script=[3])
        _, records, _ = spec(k, 3, fresh=True)
        self.assertEqual([r[0] for r in records if r[3] == readout.SOURCE_PROMPT], list(range(P)))
        self.assertIn(("prefill", tuple(IDS)), k.log)
        self.assertEqual(k.calls("prefill_cont"), [])
        self.assertEqual(k.calls("prepare"), [("prepare", tuple(IDS), 1)], "the store still prepares the turn")

    def test_the_fallback_base_path_emits_the_same_way(self):         # the drafter trails the KV (ledger R53)
        toks, records, _ = spec(ToyReadoutKernel(cached=4, feeds=False), 5)
        gen = [r for r in records if r[3] == GEN]
        self.assertEqual([r[0] for r in gen], [P + j for j in range(4)])
        self.assertTrue(all(r[2] == BOTH for r in gen))

    def test_without_a_sink_the_loop_is_unchanged(self):
        k = ToyReadoutKernel(script=[3, 0, 2])
        with contextlib.redirect_stdout(io.StringIO()):
            toks = list(toy_lm(k).spec_generate_stream(IDS, max_new_tokens=8, stop_ids=(), vb=4))
        self.assertEqual(toks, chain(IDS[-1], 8))
        self.assertEqual([c for c in k.log if c[0] in ("commit_ws", "read")], [])


class Gone(Exception):
    """What the daemon's sink raises when a readout frame cannot be sent (engine_daemon.ClientGone, Task 4)."""


class Delivery(unittest.TestCase):
    """Review ruling R5, on the host: a sink that raises stops the turn before the next token is handed out, and the
    turn's finally keeps rows only through the last position whose record went out, then commits. vb 4, script [3, ...]:
    the seed, then bursts of four."""

    def fail_on(self, kernel, call):
        """Pull tokens until the sink raises on its call-th call (0: the prompt's records). Returns (the tokens handed
        out, the positions whose records went out)."""
        calls, sent, toks = [], [], []

        def sink(count, raw):
            calls.append(count)
            if len(calls) - 1 == call:
                raise Gone()
            sent.extend(np.frombuffer(raw, LAY.dtype)["pos"].tolist())

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Gone):
            for t in ro_lm(kernel).spec_generate_stream(IDS, max_new_tokens=20, stop_ids=(), vb=4, readout_sink=sink):
                toks.append(t)
        return toks, sent

    def test_a_record_that_fails_keeps_rows_only_through_the_last_record_that_went_out(self):
        k = ToyReadoutKernel(script=[3, 3])
        toks, sent = self.fail_on(k, 1)                  # the seed's record, sent just before the burst's first draft
        self.assertEqual(toks, chain(IDS[-1], 1))
        self.assertEqual(sent, list(range(P)))
        self.assertEqual((k.kv, k.dr), (P, P), "the seed's row goes: its record never went out")
        self.assertEqual(k.calls("commit"), [("commit",)])

    def test_prompt_records_that_fail_still_commit_the_prompt(self):
        k = ToyReadoutKernel(script=[3, 3])
        self.assertEqual(self.fail_on(k, 0), ([], []))
        self.assertEqual((k.kv, k.dr), (P, P))
        self.assertEqual(k.calls("draft"), [], "no token was handed out")
        self.assertEqual(k.calls("commit"), [("commit",)])

    def test_the_fallback_cuts_the_row_of_a_token_whose_record_failed(self):     # the drafter trails the KV (R53)
        k = ToyReadoutKernel(cached=4, feeds=False)
        toks, sent = self.fail_on(k, 1)                  # the first token was stepped; then its record fails
        self.assertEqual(toks, chain(IDS[-1], 1))
        self.assertEqual(sent, list(range(4, P)), "the restored prefix has no records")
        self.assertEqual((k.kv, k.dr), (P, 4), "the stepped row goes; the drafter stays where it trails")
        self.assertEqual(k.calls("commit"), [("commit",)])

    def test_the_fallback_closed_at_a_yield_keeps_its_delivered_predecessor(self):    # Codex's re-review
        """The fallback counts a token before it hands it out, as the main path's y does. Closed at the second token's
        yield, after the first token's record went out, the turn keeps that record's row."""
        k = ToyReadoutKernel(cached=4, feeds=False)
        sent = []

        def sink(count, raw):
            sent.extend(np.frombuffer(raw, LAY.dtype)["pos"].tolist())

        with contextlib.redirect_stdout(io.StringIO()):
            gen = ro_lm(k).spec_generate_stream(IDS, max_new_tokens=20, stop_ids=(), vb=4, readout_sink=sink)
            toks = [next(gen), next(gen)]                # the first token, then the second, whose handed() sent R(P)
            gen.close()
        self.assertEqual(toks, chain(IDS[-1], 2))
        self.assertEqual(sent, list(range(4, P)) + [P])
        self.assertEqual((k.kv, k.dr), (P + 1, 4), "position P was delivered: its row stays")
        self.assertEqual(k.calls("commit"), [("commit",)])


class BaseTake(unittest.TestCase):
    def test_a_generated_take_commits_the_staged_row_first(self):
        k = ToyReadoutKernel()
        lm = ro_lm(k)
        lm.prefill(IDS)
        count, raw = lm.readout_take(0, IDS, False)
        self.assertEqual(count, P)
        self.assertTrue((np.frombuffer(raw, LAY.dtype)["flags"] == readout.FLAG_EMO).all())
        lm.step(nxt(IDS[-1]))
        count, raw = lm.readout_take(P, [nxt(IDS[-1])], True)
        self.assertEqual((count, int(np.frombuffer(raw, LAY.dtype)["flags"][0])), (1, BOTH))
        self.assertIn(("commit_ws", 0, 1), k.log)

    def test_fresh_prefills_the_whole_prompt_after_the_stores_prepare(self):
        b = FakeBackend(cached=7)
        list(b.generate_stream(list(range(10)), max_new_tokens=2, fresh=True))
        self.assertEqual(b.seen[:2], [("prepare", tuple(range(10)), False), ("prefill", tuple(range(10)))])
        b = FakeBackend(cached=7)
        b.generate(list(range(10)), max_new_tokens=2, fresh=True)
        self.assertEqual(b.seen[1], ("prefill", tuple(range(10))))

    def test_a_read_with_readouts_off_is_refused(self):
        lm = ro_lm(ToyReadoutKernel())
        lm._ro = None
        with self.assertRaises(readout.ReadoutError) as cm:
            lm.readout_take(0, [1], False)
        self.assertEqual(cm.exception.code, readout.NOT_CONFIGURED)


class ConfigAndSteer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _, self.sizes, path = build_assets(self.tmp.name)
        self.patches = [mock.patch.dict(os.environ, {"NOMOS_READOUT_REGISTRY": path}),
                        mock.patch.object(host, "_configured_hidden_size", lambda: AD)]
        for p in self.patches:
            p.start()
        self.dirs, self.lens = ids_by_kind(readout.AssetRegistry())
        self.k = ToyReadoutKernel()
        self.lm = ro_lm(self.k)
        self.lm._ro = None

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_a_configuration_is_uploaded_once_and_begins_a_turn_each_time(self):
        self.lm.readout_config([0, 2], self.dirs, [3], self.lens, 2)
        self.lm.readout_config([0, 2], self.dirs, [3], self.lens, 2)
        cfgs = self.k.calls("configure")
        self.assertEqual((len(cfgs), len(self.k.calls("begin_turn"))), (1, 2))
        _, emo, E, ws, k, dirs, has_lens, has_norm = cfgs[0]
        self.assertEqual((emo, E, ws, k, has_lens, has_norm), ([0, 2], 3, [3], 2, True, True))
        np.testing.assert_array_equal(dirs, readout.AssetRegistry().directions(self.dirs, [0, 2]))
        self.assertEqual(self.lm._ro[1], readout.Layout(2, 3, 1, 2))
        self.lm.readout_config([1], self.dirs, [], "", 0)
        self.assertEqual(len(self.k.calls("configure")), 2, "a different configuration is uploaded")
        self.assertEqual(self.lm._ro[1], readout.Layout(1, 3))

    def test_a_kernel_refusal_leaves_readouts_unconfigured(self):
        self.k.configure_rc = -1
        with self.assertRaises(readout.ReadoutError) as cm:
            self.lm.readout_config([0], self.dirs, [], "", 0)
        self.assertEqual(cm.exception.code, readout.REFUSED)
        self.assertIsNone(self.lm._ro)

    def test_an_asset_that_does_not_fit_the_model_is_refused(self):
        with mock.patch.object(host, "_configured_hidden_size", lambda: AD + 1):
            with self.assertRaises(readout.ReadoutError) as cm:
                self.lm.readout_config([0], self.dirs, [], "", 0)
        self.assertEqual(cm.exception.code, readout.REFUSED)
        self.assertEqual(self.k.calls("configure"), [])

    def test_steering_clears_then_sets_the_vector_and_its_start(self):
        self.lm.steer_set(2, self.dirs, "joyful", 0.5, 41)
        names = [c[0] for c in self.k.log if c[0] in ("clear_steer", "set_steer", "set_steer_from")]
        self.assertEqual(names, ["clear_steer", "set_steer", "set_steer_from"])
        _, layer, vec = self.k.calls("set_steer")[0]
        self.assertEqual(layer, 2)
        np.testing.assert_array_equal(vec, readout.AssetRegistry().steer_vector(self.dirs, 2, "joyful", 0.5))
        self.assertEqual(self.k.calls("set_steer_from"), [("set_steer_from", 41)])

    def test_steering_without_a_start_position_is_refused(self):
        with self.assertRaises(readout.ReadoutError) as cm:
            self.lm.steer_set(2, self.dirs, "calm", 0.5, -1)
        self.assertEqual(cm.exception.code, readout.BAD_STEER)
        self.assertEqual(self.k.calls("set_steer"), [])

    def test_a_kernel_without_readouts_refuses_every_readout_call(self):
        self.lm._readout_ok = False
        for call in (lambda: self.lm.readout_config([0], self.dirs, [], "", 0), self.lm.readout_off,
                     self.lm.steer_clear, lambda: self.lm.steer_set(2, self.dirs, "calm", 0.5, 3)):
            with self.assertRaises(readout.ReadoutError) as cm:
                call()
            self.assertEqual(cm.exception.code, readout.NO_READOUT)


class NonFiniteToyKernel(ToyReadoutKernel):
    """ToyReadoutKernel whose workspace commit does what the kernel's has done since Codex 10-26 F1: a staged position
    whose logits were non-finite stays workspace-invalid, and the commit returns -3."""

    def __init__(self, nonfinite, **kw):
        super().__init__(**kw)
        self.nonfinite = set(nonfinite)

    def nomos_readout_workspace_commit(self, h, row0, nrows):
        self.log.append(("commit_ws", row0, nrows))
        if row0 < 0 or row0 + nrows > len(self.staged):
            return -1
        rows = self.staged[row0:row0 + nrows]
        self.ws_ok.update(p for p in rows if p not in self.nonfinite)
        self.ws_ok.difference_update(p for p in rows if p in self.nonfinite)
        return -3 if any(p in self.nonfinite for p in rows) else 0


class NonFiniteWorkspace(unittest.TestCase):
    """Ledger ruling, Plan 2 Task 3: the kernel's -3 from a workspace commit (a position whose logits were non-finite
    stays workspace-invalid, Codex 10-26 F1) does not end the turn. That position's record keeps its emotion entry and
    has no workspace one, which ReadoutTurn reports as workspace_unavailable (ruling P2-R6)."""

    def test_a_non_finite_position_keeps_its_emotion_record_and_the_turn_goes_on(self):
        k = NonFiniteToyKernel({P + 2}, script=[3, 0, 2])
        toks, records, _ = spec(k, 8)
        self.assertEqual(toks, chain(IDS[-1], 8))
        flags = {pos: f for pos, _, f, src in records if src == GEN}
        self.assertEqual(sorted(flags), [P + j for j in range(7)])
        self.assertEqual(flags[P + 2], readout.FLAG_EMO, "the non-finite position has no workspace entry")
        self.assertTrue(all(f == BOTH for pos, f in flags.items() if pos != P + 2))

    def test_the_base_path_take_reads_it_the_same_way(self):
        k = NonFiniteToyKernel({P})
        lm = ro_lm(k)
        lm.prefill(IDS)
        lm.step(nxt(IDS[-1]))
        count, raw = lm.readout_take(P, [nxt(IDS[-1])], True)
        self.assertEqual((count, int(np.frombuffer(raw, LAY.dtype)["flags"][0])), (1, readout.FLAG_EMO))

    def test_other_commit_refusals_still_raise(self):
        lm = ro_lm(ToyReadoutKernel())
        lm.prefill(IDS)
        with self.assertRaises(readout.ReadoutError) as cm:
            lm._readout_commit(5)                     # more rows than the forward staged: the kernel's -1
        self.assertEqual(cm.exception.code, readout.REFUSED)


if __name__ == "__main__":
    unittest.main()
