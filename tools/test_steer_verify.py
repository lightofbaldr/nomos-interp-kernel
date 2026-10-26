#!/usr/bin/env python3
"""SP1 plan B: steering on every path, and the layer-subset capture. GPU, no drafter; run alone with the serve down:
    tools/sp1_env.sh pixi run --no-install python3 tools/test_steer_verify.py -v"""
import ctypes
import unittest

import numpy as np

from kvsession_testlib import STOP, Kernel, doc_tokens

I32, I64 = ctypes.c_int32, ctypes.c_int64
D, TL, LAYER = 6656, 52, 34
_K = None


def kernel():
    """One Glimmer per process (no drafter here: the all-layer capture clears drafter taps)."""
    global _K
    if _K is None:
        _K = Kernel({"NOMOS_KVSTORE": "0"})
        sigs = {"nomos_set_steer": [I64, I32, I64], "nomos_set_ablate": [I64, I32, I64, ctypes.c_float],
                "nomos_set_steer_from": [I64, I32], "nomos_clear_steer": [I64],
                "nomos_debug_prefill_all_layers": [I64, I64, I32, I64],
                "nomos_debug_prefill_layers": [I64, I64, I32, I32, I64, I32, I64]}
        for name, args in sigs.items():
            fn = getattr(_K.lib, name, None)
            if fn is not None:
                fn.argtypes, fn.restype = args, I32
    return _K


def tearDownModule():
    if _K is not None:
        _K.shutdown()


def all_layers(k, ids):
    a = np.ascontiguousarray(ids, np.int32)
    out = np.empty((len(a), TL, D), np.float32)
    assert k.lib.nomos_debug_prefill_all_layers(k.h, a.ctypes.data, len(a), out.ctypes.data) == 0
    return out


def steer_vec(k, scale, seed=0):
    """A random unit direction at `scale` x layer 34's mean residual norm, measured here rather than assumed."""
    norm = float(np.linalg.norm(all_layers(k, doc_tokens(64))[:, LAYER], axis=1).mean())
    v = np.random.default_rng(seed).normal(size=D)
    return np.ascontiguousarray(v / np.linalg.norm(v) * scale * norm, dtype=np.float32)


def arm(k, vec, start):
    assert k.lib.nomos_set_steer(k.h, LAYER, vec.ctypes.data) == 0
    assert k.lib.nomos_set_steer_from(k.h, start) == 0


def disarm(k):
    assert k.lib.nomos_clear_steer(k.h) == 0


class SteerFrom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()
        cls.ids = doc_tokens(200)
        cls.vec = steer_vec(cls.k, 0.5)
        cls.k.reset()
        cls.plain = cls.k.greedy(cls.k.prefill(cls.ids), 48)       # unsteered reference

    def tearDown(self):
        disarm(self.k)

    def steered(self, start):
        """Greedy under steering; start=None arms the legacy rule (no nomos_set_steer_from call)."""
        self.k.reset()
        assert self.k.lib.nomos_set_steer(self.k.h, LAYER, self.vec.ctypes.data) == 0
        if start is not None:
            assert self.k.lib.nomos_set_steer_from(self.k.h, start) == 0
        return self.k.greedy(self.k.prefill(self.ids), 48)

    def test_rejects_a_start_below_minus_one(self):
        self.assertEqual(self.k.lib.nomos_set_steer_from(self.k.h, -2), -1)

    def test_start_at_the_last_prompt_token_matches_legacy(self):
        legacy = self.steered(None)
        self.assertNotEqual(legacy, self.plain)                     # the push is strong enough to matter
        self.assertEqual(self.steered(len(self.ids) - 1), legacy)

    def test_a_later_start_leaves_the_tokens_before_it_alone(self):
        got = self.steered(len(self.ids) + 5)
        self.assertEqual(got[:6], self.plain[:6])                   # positions n-1 .. n+4 make tokens 1..6, unsteered
        self.assertNotEqual(got, self.plain)

    def test_clear_restores_the_legacy_rule(self):
        self.steered(len(self.ids) + 5)
        disarm(self.k)
        self.assertEqual(self.steered(None), self.steered(len(self.ids) - 1))

    def test_changing_the_start_while_armed_marks_the_live_kv_untracked(self):
        self.k.reset()
        assert self.k.lib.nomos_set_steer(self.k.h, LAYER, self.vec.ctypes.data) == 0
        self.k.prefill(self.ids)
        # Precondition: the tracked prefill vouches for its own write, so the live list is filled. If this fails,
        # the KV is already untracked under steering and the R17 concern this test guards is moot: rule and drop it.
        self.assertGreater(len(self.k.live_tokens()), 0)
        assert self.k.lib.nomos_set_steer_from(self.k.h, 10) == 0
        self.assertEqual(len(self.k.live_tokens()), 0)


class Batched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()
        cls.ids = doc_tokens(120)
        cls.vec = steer_vec(cls.k, 0.5, seed=1)
        cls.ref = all_layers(cls.k, cls.ids)                         # unsteered

    def tearDown(self):
        disarm(self.k)

    def test_legacy_steering_leaves_batched_rows_alone(self):
        assert self.k.lib.nomos_set_steer(self.k.h, LAYER, self.vec.ctypes.data) == 0   # steer_from stays -1
        np.testing.assert_array_equal(all_layers(self.k, self.ids), self.ref)

    def test_a_zero_vector_leaves_every_row_alone(self):
        arm(self.k, np.zeros(D, np.float32), 0)
        np.testing.assert_array_equal(all_layers(self.k, self.ids), self.ref)

    def test_rows_change_after_the_steer_layer_and_from_the_start(self):
        arm(self.k, self.vec, 40)
        got = all_layers(self.k, self.ids)
        np.testing.assert_array_equal(got[:, :LAYER + 1], self.ref[:, :LAYER + 1])   # the tap is copied before the add
        np.testing.assert_array_equal(got[:40], self.ref[:40])                       # earlier rows never see it
        self.assertGreater(float(np.abs(got[40:, LAYER + 1] - self.ref[40:, LAYER + 1]).max()), 1.0)

    def test_continuation_rows_are_steered(self):
        fb = doc_tokens(60)[1:]                                       # 59 tokens of "feedback", no BOS

        def continued(start):
            self.k.reset()
            assert self.k.lib.nomos_set_steer(self.k.h, LAYER, self.vec.ctypes.data) == 0
            if start is not None:
                assert self.k.lib.nomos_set_steer_from(self.k.h, start) == 0
            self.k.greedy(self.k.prefill(self.ids), 8)
            return self.k.prefill_cont(fb)

        legacy = continued(None)                  # legacy: only the continuation's last row is steered
        every = continued(len(self.ids) - 1)      # with a start: every continuation row is steered
        self.assertGreater(float(np.abs(every - legacy).max()), 0.0)

    def test_ablate_reaches_batched_rows_and_alpha_zero_is_a_no_op(self):
        unit = np.ascontiguousarray(self.vec / np.linalg.norm(self.vec), dtype=np.float32)
        assert self.k.lib.nomos_set_ablate(self.k.h, LAYER, unit.ctypes.data, ctypes.c_float(0.0)) == 0
        assert self.k.lib.nomos_set_steer_from(self.k.h, 0) == 0
        np.testing.assert_array_equal(all_layers(self.k, self.ids), self.ref)
        disarm(self.k)
        assert self.k.lib.nomos_set_ablate(self.k.h, LAYER, unit.ctypes.data, ctypes.c_float(1.0)) == 0
        assert self.k.lib.nomos_set_steer_from(self.k.h, 40) == 0
        got = all_layers(self.k, self.ids)
        np.testing.assert_array_equal(got[:40], self.ref[:40])
        self.assertGreater(float(np.abs(got[40:, LAYER + 1] - self.ref[40:, LAYER + 1]).max()), 1e-3)


def layers(k, ids, lay, start=0):
    a, l = np.ascontiguousarray(ids, np.int32), np.ascontiguousarray(lay, np.int32)
    out = np.empty((len(a), len(l), D), np.float32)
    rc = k.lib.nomos_debug_prefill_layers(k.h, a.ctypes.data, len(a), start, l.ctypes.data, len(l), out.ctypes.data)
    assert rc == 0, f"nomos_debug_prefill_layers rc={rc}"
    return out


class LayerCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()

    def test_matches_the_all_layer_capture(self):
        ids = doc_tokens(200)
        ref = all_layers(self.k, ids)
        got = layers(self.k, ids, [LAYER, 5])
        np.testing.assert_array_equal(got[:, 0], ref[:, LAYER])
        np.testing.assert_array_equal(got[:, 1], ref[:, 5])

    def test_chunked_capture_continues_the_cache(self):
        # Batched states depend on the batch size unless it is a multiple of 256 (measured 10-19: a 300-row batch vs
        # the first 300 rows of a 600-row one differ from layer 0 on, and W4A4 grows that to a median cosine of 0.987
        # by layer 34). So the chunks are 256-aligned, and the first is bit-exact. The second goes through the offset
        # attention route, which differs from the batched route in the last bits: layer 0 is where that is still
        # negligible, and where a chunk that ignored the cache shows at once.
        ids = doc_tokens(512)
        lay = [LAYER, 0]
        one = layers(self.k, ids, lay)
        a = layers(self.k, ids[:256], lay, start=0)
        b = layers(self.k, ids[256:], lay, start=256)
        np.testing.assert_array_equal(a, one[:256])

        def cos(x, y):
            return (x * y).sum(1) / (np.linalg.norm(x, axis=1) * np.linalg.norm(y, axis=1))

        self.assertGreater(float(cos(b[:, 1], one[256:, 1]).min()), 0.9999)            # measured 0.99998
        self.assertGreater(float(np.median(cos(b[:, 0], one[256:, 0]))), 0.97)        # measured 0.985 at layer 34
        nc = layers(self.k, ids[256:], lay, start=0)                                   # the same rows, no cache
        self.assertLess(float(cos(nc[:, 1], one[256:, 1]).min()), 0.99)                # measured 0.57: the check bites
        self.assertLess(float(np.median(cos(nc[:, 0], one[256:, 0]))), 0.97)          # measured 0.950

    def test_rejects_bad_layers_and_positions(self):
        k = self.k
        a = np.ascontiguousarray(doc_tokens(20), np.int32)
        out = np.empty((20, 2, D), np.float32)

        def rc(lay, n=20, start=0):
            l = np.ascontiguousarray(lay, np.int32)
            return k.lib.nomos_debug_prefill_layers(k.h, a.ctypes.data, n, start, l.ctypes.data, len(l),
                                                    out.ctypes.data)

        self.assertEqual(rc([5, 5]), -3)
        self.assertEqual(rc([TL]), -3)
        self.assertEqual(rc([-1]), -3)
        k.reset()
        self.assertEqual(rc([5], start=10), -2)                  # the cache does not hold 10 tokens
        self.assertEqual(rc([5], n=k.max_seq + 1), -2)          # refused before any id is read

    def test_a_chunk_that_holds_the_start_is_steered_from_it(self):
        """The serve's shape (final review I1a): under steering the store steps aside (R17) and each turn re-prefills the
        conversation in chunks (NOMOS_PREFILL_CHUNK), so the chunk that holds steer_from_pos starts past 0 with the start
        inside it. Rows before the start match the unsteered chunk bit for bit; every row from the start on changes after
        the steer layer; at the steer layer itself the tap is copied before the add, so no row changes there."""
        ids = doc_tokens(512)
        lay = [LAYER, LAYER + 1]
        vec = steer_vec(self.k, 0.5, seed=4)
        plain_a = layers(self.k, ids[:256], lay, start=0)
        plain_b = layers(self.k, ids[256:], lay, start=256)
        arm(self.k, vec, 300)
        try:
            got_a = layers(self.k, ids[:256], lay, start=0)            # the start lies past this chunk
            got_b = layers(self.k, ids[256:], lay, start=256)          # positions 256..511; the start is row 44
        finally:
            disarm(self.k)
        np.testing.assert_array_equal(got_a, plain_a)
        np.testing.assert_array_equal(got_b[:44], plain_b[:44])
        np.testing.assert_array_equal(got_b[:, 0], plain_b[:, 0])
        moved = np.abs(got_b[44:, 1] - plain_b[44:, 1]).max(1)          # per row: an off-by-one leaves row 44 at 0
        self.assertGreater(float(moved.min()), 1.0)


if __name__ == "__main__":
    unittest.main()
