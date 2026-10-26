"""G5's validity gate for the first-cycle fused-verify tap trace (CPU only; no library is loaded).

Codex's 10-24 review ran the old gate against fake FFI methods. It accepted a getter that returned -99, a buffer of NaN,
and a buffer with only one tap slot written: it rejected only rc 0 with a global mean of zero. A trace is now published
as valid only when the getter succeeded and every expected value was written and finite."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import muse_spec_e2e as E  # noqa: E402


def written(value=0.5):
    return np.full((E.TAPS, E.D), value, np.float32)


def unwritten():
    return np.full((E.TAPS, E.D), np.nan, np.float32)


class G5Check(unittest.TestCase):
    def test_a_successful_fully_written_read_passes(self):
        ok, reason, slots = E.g5_check(0, written())
        self.assertTrue(ok, reason)
        self.assertEqual([s["slot"] for s in slots], list(range(E.TAPS)))

    def test_a_failed_getter_is_rejected_even_when_the_buffer_looks_written(self):
        ok, reason, _ = E.g5_check(-99, written())
        self.assertFalse(ok)
        self.assertIn("rc=-99", reason)

    def test_an_unwritten_buffer_is_rejected(self):
        ok, reason, _ = E.g5_check(0, unwritten())
        self.assertFalse(ok)
        self.assertIn("non-finite or unwritten", reason)

    def test_a_buffer_with_one_slot_written_is_rejected(self):
        taps = unwritten()
        taps[0] = 0.5
        ok, reason, slots = E.g5_check(0, taps)
        self.assertFalse(ok)
        self.assertEqual([s["slot"] for s in slots if not s["finite"]], list(range(1, E.TAPS)))

    def test_an_infinity_is_rejected(self):
        taps = written()
        taps[1, 7] = np.inf
        ok, reason, _ = E.g5_check(0, taps)
        self.assertFalse(ok)
        self.assertIn("slot(s) [1]", reason)

    def test_an_all_zero_slot_is_rejected(self):
        taps = written()
        taps[2] = 0.0
        ok, reason, _ = E.g5_check(0, taps)
        self.assertFalse(ok)
        self.assertIn("all-zero tap in slot 2", reason)

    def test_a_wrong_shape_is_rejected(self):
        ok, reason, _ = E.g5_check(0, np.full((E.TAPS - 1, E.D), 0.5, np.float32))
        self.assertFalse(ok)
        self.assertIn("shape", reason)


class FakeLib:
    """Just enough FFI for spec_generate's first cycle: every draft is accepted, and the tap getter's behavior is
    the variable. Pointers are numpy buffer addresses, as with the real library."""

    def __init__(self, taps_mode):
        self.taps_mode, self.kv, self.dr = taps_mode, 0, 0

    @staticmethod
    def _arr(addr, n, ctype):
        import ctypes
        return np.ctypeslib.as_array((ctype * n).from_address(addr))

    def nomos_reset_kv(self, ht): self.kv = 0; return 0
    def nomos_dflash_reset(self, ht): self.dr = 0; return 0
    def nomos_kv_cache_len(self, ht): return self.kv
    def nomos_dflash_cache_len(self, ht): return self.dr
    def nomos_kv_set_len(self, ht, n): self.kv = int(n.value if hasattr(n, "value") else n); return 0

    def nomos_prefill(self, ht, ids_addr, n, logits_addr):
        import ctypes
        self._arr(logits_addr, E.VOCAB, ctypes.c_float)[:] = 0.0
        self.kv = self.dr = int(n)
        return 0

    def nomos_dflash_draft_block(self, ht, c, start, dout_addr):
        import ctypes
        self._arr(dout_addr, E.BLOCK, ctypes.c_int32)[:] = np.arange(1, E.BLOCK + 1)
        return E.BLOCK - 1

    def nomos_dflash_verify_fused(self, ht, toks_addr, vb, start, rows_addr):
        import ctypes
        vb = int(vb.value if hasattr(vb, "value") else vb)
        rows = self._arr(rows_addr, (E.BLOCK + 1) * E.VOCAB, ctypes.c_float).reshape(E.BLOCK + 1, E.VOCAB)
        rows[:] = 0.0
        for j in range(vb):
            rows[j, j + 2] = 1.0          # argmax(rows[j]) == drafts[j] == j + 2: every draft accepted
        self.kv += vb
        return 0

    def nomos_dflash_get_taps(self, ht, out_addr):
        import ctypes
        out = self._arr(out_addr, E.TAPS * E.D, ctypes.c_float).reshape(E.TAPS, E.D)
        if self.taps_mode == "valid":
            out[:] = 0.5
        elif self.taps_mode == "rc-99":
            return -99
        elif self.taps_mode == "nan":
            out[:] = np.nan
        elif self.taps_mode == "one-slot":
            out[0] = 0.5
        return 0

    def nomos_dflash_append_verify_context(self, ht, a, n):
        n = int(n.value if hasattr(n, "value") else n)
        self.dr = self.kv
        return n


class G5ThroughSpecGenerate(unittest.TestCase):
    """The repaired gate inside the real spec_generate: only a good read publishes a trace."""

    def run_mode(self, mode):
        import tempfile
        d = tempfile.mkdtemp(prefix="g5-")
        trace = os.path.join(d, "first_cycle_trace.npz")
        logits = np.zeros(E.VOCAB, np.float32)
        ids = np.array([5, 6, 7, 8], np.int32)
        try:
            E.spec_generate(FakeLib(mode), 1, logits, ids, ntok=12, vb=8, trace_path=trace)
            exited = None
        except SystemExit as e:
            exited = e.code
        return exited, os.path.exists(trace), os.path.exists(trace.replace(".npz", ".INVALID.npz")), trace

    def test_a_valid_read_publishes_a_labelled_trace(self):
        exited, valid, invalid, trace = self.run_mode("valid")
        self.assertEqual((exited, valid, invalid), (None, True, False))
        z = np.load(trace)
        self.assertEqual(str(z["capture_kind"]), "fused-verify")
        self.assertEqual(int(z["abs_position"]), 4 + 7)

    def test_bad_reads_fail_g5_and_are_saved_only_as_invalid(self):
        for mode in ("rc-99", "nan", "one-slot"):
            with self.subTest(mode=mode):
                exited, valid, invalid, _ = self.run_mode(mode)
                self.assertEqual((exited, valid, invalid), (2, False, True))


if __name__ == "__main__":
    unittest.main()
