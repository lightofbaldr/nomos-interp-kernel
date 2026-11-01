#!/usr/bin/env python3
"""A steered conversation continues its own live KV (Adam 11-01). GPU; run alone:
pixi run --no-install python3 tools/test_kv_session_steered.py -v

While steering is armed the store stays bypassed (ledger R17). A later call of the same steered conversation continues
the live KV computed under the steering armed now, as an unsteered call continues the store's live session. Any change
of steering, clearing it, and the legacy decode-only rule still start from a clean KV, and steered KV never reaches the
store."""
import ctypes
import json
import os
import shutil
import unittest

import numpy as np

from kvsession_testlib import ROOT, Kernel, doc_tokens

DIR = ROOT / "build/kvstore_steered_test"
DIRS = os.path.expanduser("~/nomos_data/readout-assets/directions-sp1")
LAYER = 24


def steer_vector(name, s):
    """s x layer_sizes[LAYER] x the unit direction, readout.AssetRegistry.steer_vector's units."""
    with open(os.path.join(DIRS, "manifest.json")) as f:
        man = json.load(f)
    units = np.fromfile(os.path.join(DIRS, f"dirs_L{LAYER}.f32"), dtype=np.float32).reshape(man["E"], man["D"])
    unit = units[man["names"].index(name)].astype(np.float64)
    return np.ascontiguousarray(s * man["layer_sizes"][LAYER] * unit, dtype=np.float32)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


class SteeredSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(shutil.rmtree, DIR, ignore_errors=True)
        shutil.rmtree(DIR, ignore_errors=True)
        cls.k = Kernel({"NOMOS_KVSTORE": "1", "NOMOS_KVSTORE_DIR": str(DIR)})
        lib = cls.k.lib
        lib.nomos_set_steer.argtypes, lib.nomos_set_steer.restype = [ctypes.c_int64, ctypes.c_int32, ctypes.c_int64], ctypes.c_int32
        lib.nomos_set_steer_from.argtypes, lib.nomos_set_steer_from.restype = [ctypes.c_int64, ctypes.c_int32], ctypes.c_int32
        lib.nomos_clear_steer.argtypes, lib.nomos_clear_steer.restype = [ctypes.c_int64], ctypes.c_int32
        cls.fear, cls.fear2 = steer_vector("afraid", 0.5), steer_vector("afraid", 0.6)

    @classmethod
    def tearDownClass(cls):
        cls.k.shutdown()

    def setUp(self):
        self.assertEqual(self.k.lib.nomos_clear_steer(self.k.h), 0)
        self.k.reset()

    def arm(self, vec, from_pos=0):
        """What the serve does for every steered request: clear, set the vector, set where it starts."""
        self.assertEqual(self.k.lib.nomos_clear_steer(self.k.h), 0)
        self.assertEqual(self.k.lib.nomos_set_steer(self.k.h, LAYER, vec.ctypes.data), 0)
        if from_pos is not None:
            self.assertEqual(self.k.lib.nomos_set_steer_from(self.k.h, from_pos), 0)

    def turn(self, ids, n_gen=8):
        """One call through the store, as the serve runs it. Returns (c, the tokens the engine holds afterwards)."""
        c = self.k.prepare(ids)
        self.assertGreaterEqual(c, 0)
        self.assertEqual(self.k.cache_len(), c)
        self.assertEqual(self.k.live_tokens(), list(ids[:c]))      # never KV under the wrong tokens
        lg = self.k.prefill(ids) if c == 0 else self.k.prefill_cont(ids[c:])
        self.k.greedy(lg, n_gen)
        self.assertGreaterEqual(self.k.commit(), 0)
        held = self.k.live_tokens()
        self.assertEqual(len(held), self.k.cache_len())
        return c, held

    def plain_continuation_gap(self, a, b):
        """1 - cos(continued, fresh) for an unsteered conversation: prefill a, generate, continue with b, against one
        fresh prefill of the same tokens. A continued prefix never matched a fresh prefill exactly on this engine
        (INT8 KV written by other prefill shapes): 0.0031 on 11-01, and the store's own reuse has the same gap."""
        self.k.reset()
        held = (self.k.greedy(self.k.prefill(a), 8), self.k.live_tokens())[1]
        cont = self.k.prefill_cont(b)
        self.k.reset()
        return 1 - cos(cont, self.k.prefill(held + b))

    def test_a_steered_conversation_continues_its_own_live_kv(self):
        a, b = doc_tokens(400)[100:], doc_tokens(480)[400:]
        plain_gap = self.plain_continuation_gap(a, b)
        self.arm(self.fear)
        c1, held = self.turn(a)
        self.assertEqual(c1, 0)                              # arming untracked whatever the engine held
        before = self.k.stats()
        ids2 = held + b                                      # the next call: the conversation so far plus a new message
        c2 = self.k.prepare(ids2)
        self.assertEqual(c2, len(held))                      # the whole live KV is continued ...
        self.assertEqual(self.k.live_tokens(), held)
        self.assertEqual(self.k.stats(), before)             # ... without the store (no prepare counted, no restore)
        cont = self.k.prefill_cont(ids2[c2:])
        self.k.reset()
        fresh = self.k.prefill(ids2)                         # the same conversation prefilled from scratch, steered
        self.assertEqual(self.k.lib.nomos_clear_steer(self.k.h), 0)
        self.k.reset()
        plain = self.k.prefill(ids2)                         # and unsteered
        # As faithful as an unsteered continuation (0.0049 against 0.0031 on 11-01), and steered: closer to the fresh
        # steered prefill than to the plain one (steering itself moves these logits by 1 - cos = 0.019).
        self.assertLess(1 - cos(cont, fresh), 2 * plain_gap + 1e-3)
        self.assertGreater(cos(cont, fresh), cos(cont, plain))
        self.assertIn(int(np.argmax(cont)), np.argsort(fresh)[-3:])

    def test_changing_the_steering_starts_clean(self):
        self.arm(self.fear)
        _, held = self.turn(doc_tokens(700)[400:])
        self.arm(self.fear2)                                 # another strength
        self.assertEqual(self.k.prepare(held + doc_tokens(760)[700:]), 0)
        self.assertEqual(self.k.cache_len(), 0)

    def test_rearming_identical_steering_through_the_kernel_starts_clean(self):
        """The kernel cannot tell an identical re-arm from a change: the host must not re-arm (pyhost KernelLM.steer_set)."""
        self.arm(self.fear)
        _, held = self.turn(doc_tokens(1000)[700:])
        self.arm(self.fear)
        self.assertEqual(self.k.prepare(held + doc_tokens(1060)[1000:]), 0)

    def test_clearing_never_continues_steered_kv(self):
        self.arm(self.fear)
        _, held = self.turn(doc_tokens(1300)[1000:])         # tokens no plain session in this store holds
        self.assertEqual(self.k.lib.nomos_clear_steer(self.k.h), 0)
        self.assertEqual(self.k.prepare(held + doc_tokens(1360)[1300:]), 0)
        self.assertEqual(self.k.cache_len(), 0)

    def test_steering_never_continues_plain_kv(self):
        _, held = self.turn(doc_tokens(1600)[1300:])         # an unsteered call, committed to the store
        self.arm(self.fear)
        self.assertEqual(self.k.prepare(held + doc_tokens(1660)[1600:]), 0)
        self.assertEqual(self.k.cache_len(), 0)

    def test_response_steering_starts_clean_each_call(self):
        """The serve's default starts at each call's last prompt token, which moves every call."""
        ids1 = doc_tokens(1900)[1600:]
        self.arm(self.fear, from_pos=len(ids1) - 1)
        _, held = self.turn(ids1)
        ids2 = held + doc_tokens(1960)[1900:]
        self.assertEqual(self.k.lib.nomos_set_steer_from(self.k.h, len(ids2) - 1), 0)
        self.assertEqual(self.k.prepare(ids2), 0)

    def test_the_legacy_decode_only_rule_starts_clean(self):
        self.arm(self.fear, from_pos=None)                   # no start position: decode-only steering
        _, held = self.turn(doc_tokens(2200)[1900:])
        self.assertEqual(self.k.prepare(held + doc_tokens(2260)[2200:]), 0)


if __name__ == "__main__":
    unittest.main()
