#!/usr/bin/env python3
"""SP1 plan B: steering under DFlash's verify pass. GPU with the drafter; run alone with the serve down:
    tools/sp1_env.sh pixi run --no-install python3 tools/test_steer_verify_spec.py -v"""
import ctypes
import unittest

import numpy as np

from kvsession_testlib import VOCAB, Kernel, doc_tokens

I32, I64 = ctypes.c_int32, ctypes.c_int64
D, TL, LAYER = 6656, 52, 34
_K, _NORM = None, None


def kernel():
    """One Glimmer + drafter per process. Layer 34's mean residual norm is measured BEFORE the drafter loads:
    the all-layer capture clears the drafter's taps."""
    global _K, _NORM
    if _K is None:
        _K = Kernel({"NOMOS_KVSTORE": "0"})
        sigs = {"nomos_set_steer": [I64, I32, I64], "nomos_set_steer_from": [I64, I32], "nomos_clear_steer": [I64],
                "nomos_debug_prefill_all_layers": [I64, I64, I32, I64],
                "nomos_debug_prefill_layers": [I64, I64, I32, I32, I64, I32, I64]}
        for name, args in sigs.items():
            fn = getattr(_K.lib, name, None)
            if fn is not None:
                fn.argtypes, fn.restype = args, I32
        a = np.ascontiguousarray(doc_tokens(64), np.int32)
        out = np.empty((len(a), TL, D), np.float32)
        assert _K.lib.nomos_debug_prefill_all_layers(_K.h, a.ctypes.data, len(a), out.ctypes.data) == 0
        _NORM = float(np.linalg.norm(out[:, LAYER], axis=1).mean())
        _K.dflash_load()
    return _K


def tearDownModule():
    if _K is not None:
        _K.shutdown()


def vec(scale, seed):
    v = np.random.default_rng(seed).normal(size=D)
    return np.ascontiguousarray(v / np.linalg.norm(v) * scale * _NORM, dtype=np.float32)


def fresh(k):
    k.reset()
    assert k.lib.nomos_dflash_reset(k.h) == 0


class Spec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()
        cls.ids = doc_tokens(200)

    def tearDown(self):
        assert self.k.lib.nomos_clear_steer(self.k.h) == 0

    def test_steered_spec_matches_steered_plain(self):
        for scale in (0.05, 0.5):
            v, out = vec(scale, seed=2), {}
            for path in ("plain", "spec"):
                fresh(self.k)
                assert self.k.lib.nomos_set_steer(self.k.h, LAYER, v.ctypes.data) == 0
                assert self.k.lib.nomos_set_steer_from(self.k.h, len(self.ids) - 1) == 0
                lg = self.k.prefill(self.ids)
                out[path] = self.k.greedy(lg, 64) if path == "plain" else self.k.spec_trace(lg, max_new=64)[0]
            self.assertEqual(out["spec"], out["plain"], f"scale {scale}")

    def test_a_zero_vector_changes_nothing_on_either_path(self):
        fresh(self.k)
        unsteered = self.k.greedy(self.k.prefill(self.ids), 64)
        zero = np.zeros(D, np.float32)
        for path in ("plain", "spec"):
            fresh(self.k)
            assert self.k.lib.nomos_set_steer(self.k.h, LAYER, zero.ctypes.data) == 0
            assert self.k.lib.nomos_set_steer_from(self.k.h, len(self.ids) - 1) == 0
            lg = self.k.prefill(self.ids)
            got = self.k.greedy(lg, 64) if path == "plain" else self.k.spec_trace(lg, max_new=64)[0]
            assert self.k.lib.nomos_clear_steer(self.k.h) == 0
            self.assertEqual(got, unsteered, path)


    def test_an_episode_with_a_fed_back_message_is_identical(self):
        """Spec 7.1's episode check (final review I1b): an answer, a failure message fed back through continuation
        prefill, the next answer, all under steering, DFlash against plain greedy. Both paths are cut to the prompt and
        the first answer but its last token before the message (spec_trace's end state can keep one more row, R54), and
        that token rides in with the message, as the host carries it into the next turn."""
        feedback = doc_tokens(400)[300:340]
        v, out = vec(0.5, seed=5), {}
        for path in ("plain", "spec"):
            fresh(self.k)
            assert self.k.lib.nomos_set_steer(self.k.h, LAYER, v.ctypes.data) == 0
            assert self.k.lib.nomos_set_steer_from(self.k.h, len(self.ids) - 1) == 0
            gen = (lambda lg, n: self.k.greedy(lg, n)) if path == "plain" else \
                  (lambda lg, n: self.k.spec_trace(lg, max_new=n)[0])
            first = gen(self.k.prefill(self.ids), 24)
            p = len(self.ids) + len(first) - 1
            self.assertEqual(self.k.truncate(p), 0)
            self.assertEqual(self.k.cache_len(), p)
            second = gen(self.k.prefill_cont([first[-1]] + list(feedback)), 64)
            out[path] = (first, second)
        self.assertEqual(out["spec"][0], out["plain"][0], "the first answer")
        self.assertEqual(out["spec"][1], out["plain"][1], "the answer after the fed-back message")

class Guard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()
        cls.ids = doc_tokens(100)

    def test_verify_refuses_steering_without_a_start(self):
        k = self.k
        fresh(k)
        lg = k.prefill(self.ids)
        v = vec(0.5, seed=3)
        assert k.lib.nomos_set_steer(k.h, LAYER, v.ctypes.data) == 0          # armed, but no nomos_set_steer_from
        rows = np.array([int(np.argmax(lg))] * 2, np.int32)
        vlog = np.zeros((2, VOCAB), np.float32)
        rc = k.lib.nomos_dflash_verify_fused(k.h, rows.ctypes.data, 2, k.cache_len(), vlog.ctypes.data)
        assert k.lib.nomos_clear_steer(k.h) == 0
        self.assertEqual(rc, -4)


class TapsSurvive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = kernel()
        cls.ids = doc_tokens(100)

    def test_drafting_works_after_a_layer_capture(self):
        k = self.k
        a, l = np.ascontiguousarray(self.ids[:50], np.int32), np.array([LAYER], np.int32)
        out = np.empty((50, 1, D), np.float32)
        self.assertEqual(k.lib.nomos_debug_prefill_layers(k.h, a.ctypes.data, 50, 0, l.ctypes.data, 1,
                                                          out.ctypes.data), 0)
        fresh(k)
        toks, acc = k.spec_trace(k.prefill(self.ids), max_new=32)     # asserts draft_block == 15 every cycle
        self.assertGreater(len(toks), 1)
        self.assertGreater(sum(acc), 0)                               # the drafter still gets drafts accepted


if __name__ == "__main__":
    unittest.main()
