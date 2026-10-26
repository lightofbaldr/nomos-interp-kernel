"""Readout spine kernel tests (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 2).
GPU, with the serve down, on the library the last build recorded. From the worktree root (the plan's test command):
  read -r SO_SHA256 SO < /home/adam/nomos_data/readout-spine/build/so.sha256 && echo "$SO_SHA256  $SO" | sha256sum -c - &&
  SO_PATH="$SO" READOUT_SO_SHA256="$SO_SHA256" /home/adam/Projects/glimmer-emotions/scripts/with_serve_down.sh LOG -- \
    tools/sp1_env.sh pixi run --no-install python3 tools/test_readout.py -v
tools/sp1_env.sh sets NOMOS_PREFILL_CHUNK=256 and NOMOS_KVSTORE=0; kvsession_testlib.Kernel sets NOMOS_MAX_SEQ=8192.
The CPU-only classes (BitOracle, BenchGate) never load the library.
"""
import contextlib
import ctypes
import hashlib
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kvsession_testlib as T  # noqa: E402
from test_kv_session_build import expected_build_id  # noqa: E402

I32, I64, F32 = ctypes.c_int32, ctypes.c_int64, ctypes.c_float
D, LAYERS, E = 6656, 52, 39
EMO, WS, K = [20, 24, 29], [36, 40, 44, 48], 8
TAPS = [1, 13, 25, 37, 49]        # the Muse DFlash tap layers (lib/model_config.mojo DRAFTER_TAP_0..4), in slot order
ASSETS = os.path.expanduser("~/nomos_data/readout-assets/lens-muse-glimmer")
SIGS = {
    "nomos_readout_configure": [I64, I64, I32, I64, I32, I64, I32, I64, I64, I32],
    "nomos_readout_disable": [I64],
    "nomos_readout_begin_turn": [I64],
    "nomos_readout_read": [I64, I32, I32, I64, I64, I64, I64, I64],
    "nomos_readout_workspace_commit": [I64, I32, I32],
    "nomos_readout_project_rows": [I64, I64, I32, I32, I64],
    "nomos_readout_debug_topk": [I64, I64, I32, I64, I64],
    "nomos_readout_debug_stage": [I64, I64],
    "nomos_debug_prefill_layers": [I64, I64, I32, I32, I64, I32, I64],
    "nomos_debug_prefill_all_layers": [I64, I64, I32, I64],
    "nomos_set_steer": [I64, I32, I64],
    "nomos_set_ablate": [I64, I32, I64, F32],
    "nomos_set_steer_from": [I64, I32],
    "nomos_clear_steer": [I64],
    "nomos_dflash_get_taps": [I64, I64],
}
PROMPT = (np.arange(600) * 7919 % 50000 + 1000).astype(np.int32)   # deterministic synthetic token ids
LIBRARY = {}      # the loaded library's identity (path, sha256, build_id), filled by kernel()
_K = None
_K_ERR = None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def mapped_kernel_libraries():
    """Every libnomos_kernel file this process has mapped, as /proc/self/maps names it."""
    found = set()
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split(None, 5)
            if len(parts) == 6 and os.path.basename(parts[5].strip()).startswith("libnomos_kernel"):
                found.add(parts[5].strip())
    return sorted(found)


def _load_checked():
    """Review R8: loads exactly the library the last build recorded, and proves it. The test command passes SO_PATH
    (which kvsession_testlib loads) and READOUT_SO_SHA256 (the sha256 the build recorded).
    - Before the load, the file must hash to READOUT_SO_SHA256.
    - After it, the process must map exactly that file, and the file must still hash to it.
    - The library's BUILD_ID must equal the hash of this worktree's sources (tools/test_kv_session_build.py's recipe of
      refresh_build.sh's), so a library older than the sources is refused too."""
    path, want = os.path.realpath(T.SO), os.environ.get("READOUT_SO_SHA256", "")
    if len(want) != 64:
        raise RuntimeError("READOUT_SO_SHA256 is not set: run the tests with the plan's test command")
    if not os.path.isfile(path) or sha256_file(path) != want:
        raise RuntimeError(f"{path} is not the library the last build recorded (sha256 {want})")
    k = T.Kernel()
    mapped = mapped_kernel_libraries()
    if mapped != [path] or sha256_file(path) != want:
        raise RuntimeError(f"the process mapped {mapped}, not exactly {path} with sha256 {want}")
    k.lib.nomos_build_id.restype = I64
    built, source = k.lib.nomos_build_id() & 0xFFFFFFFFFFFFFFFF, expected_build_id()
    if built != source:
        raise RuntimeError(f"the library's BUILD_ID {built:#018x} is not the worktree sources' {source:#018x}: rebuild")
    LIBRARY.update(path=path, sha256=want, build_id=f"{built:#018x}")
    print(f"[test_readout] library {path} sha256 {want} BUILD_ID {built:#018x}", file=sys.stderr, flush=True)
    return k


def kernel():
    """One engine per process (it holds the GPU), on the checked library. Exports an older build lacks bind as None.
    An identity failure is kept, so later tests report it instead of loading a second engine."""
    global _K, _K_ERR
    if _K_ERR is not None:
        raise RuntimeError(_K_ERR)
    if _K is None:
        try:
            k = _load_checked()
        except RuntimeError as e:
            _K_ERR = str(e)
            raise
        for name, args in SIGS.items():
            fn = getattr(k.lib, name, None)
            if fn is not None:
                fn.argtypes, fn.restype = args, I32
        _K = k
    return _K


def assert_finite(a, what):
    """Review R8: a bit claim is made on finite values only."""
    a = np.asarray(a)
    bad = int(a.size - np.count_nonzero(np.isfinite(a)))
    if bad:
        raise AssertionError(f"{what}: {bad} non-finite values")


def assert_bits_equal(got, want, what):
    """Review R8's literal bit claim: both arrays float32 with one shape, every value finite, and identical float32 bit
    patterns. np.testing.assert_array_equal alone accepts matched NaNs and infinities, and +0.0 against -0.0."""
    got, want = np.asarray(got), np.asarray(want)
    if got.dtype != np.float32 or want.dtype != np.float32:
        raise AssertionError(f"{what}: dtypes {got.dtype} and {want.dtype}, not float32")
    if got.shape != want.shape:
        raise AssertionError(f"{what}: shapes {got.shape} and {want.shape}")
    assert_finite(got, what + " (got)")
    assert_finite(want, what + " (want)")
    g = np.ascontiguousarray(got).view(np.uint32)
    w = np.ascontiguousarray(want).view(np.uint32)
    diff = np.flatnonzero(g != w)
    if diff.size:
        raise AssertionError(f"{what}: {diff.size} of {g.size} float32 bit patterns differ, first at flat index {diff[0]}")


def ensure_drafter(k):
    """Loads the DFlash drafter once per process (kvsession_testlib adds the trailing slash its loader needs)."""
    if getattr(k, "_ddir", None) is None:
        k.dflash_load()


def unit_dirs(n_layers, seed=0):
    """Random unit directions [n_layers, E, D] in float32, normalized in float64."""
    v = np.random.default_rng(seed).standard_normal((n_layers, E, D))
    v /= np.linalg.norm(v, axis=-1, keepdims=True)
    return np.ascontiguousarray(v, dtype=np.float32)


@contextlib.contextmanager
def env(**values):
    """Sets engine flags for the calls inside the block (the engine reads them per call), then restores them."""
    old = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class Hygiene(unittest.TestCase):
    """The spec's two hygiene fixes."""

    def test_set_steer_after_ablate_is_additive(self):
        k = kernel()
        d_hat = unit_dirs(1, seed=7)[0, 0]
        vec = np.ascontiguousarray(d_hat * 4.0, np.float32)
        ids = PROMPT[:64]
        k.lib.nomos_clear_steer(k.h)
        self.assertEqual(k.lib.nomos_set_steer(k.h, 24, vec.ctypes.data), 0)
        self.assertEqual(k.lib.nomos_set_steer_from(k.h, 0), 0)
        fresh = k.prefill(ids)
        k.lib.nomos_clear_steer(k.h)
        self.assertEqual(k.lib.nomos_set_ablate(k.h, 24, d_hat.ctypes.data, 1.0), 0)
        self.assertEqual(k.lib.nomos_set_steer(k.h, 24, vec.ctypes.data), 0)
        self.assertEqual(k.lib.nomos_set_steer_from(k.h, 0), 0)
        after = k.prefill(ids)
        k.lib.nomos_clear_steer(k.h)
        assert_bits_equal(after, fresh, "logits after an ablate then a set, against a fresh set")

    def test_all_layers_capture_keeps_the_drafter_taps(self):
        k = kernel()
        ensure_drafter(k)
        taps = np.zeros((len(TAPS), D), np.float32)
        k.prefill(PROMPT[:40])
        k.step(int(PROMPT[40]))
        self.assertEqual(k.lib.nomos_dflash_get_taps(k.h, taps.ctypes.data), 0)
        before = taps.copy()
        ids = np.ascontiguousarray(PROMPT[100:108], np.int32)
        out = np.zeros((len(ids), LAYERS, D), np.float32)
        self.assertEqual(k.lib.nomos_debug_prefill_all_layers(k.h, ids.ctypes.data, len(ids), out.ctypes.data), 0)
        k.prefill(PROMPT[200:240])
        k.step(int(PROMPT[240]))
        self.assertEqual(k.lib.nomos_dflash_get_taps(k.h, taps.ctypes.data), 0)
        assert_finite(before, "the first decode's taps")
        assert_finite(taps, "the second decode's taps")
        self.assertNotEqual(taps.tobytes(), before.tobytes(), "the decode step no longer writes the drafter taps")


class BitOracle(unittest.TestCase):
    """CPU only: controls for the bit checker itself (review R8). They never load the library."""

    def test_numeric_equality_alone_accepts_the_witness(self):
        a, b = np.array([0.0, np.nan], np.float32), np.array([-0.0, np.nan], np.float32)
        np.testing.assert_array_equal(a, b)                  # the reviewed oracle passes this pair...
        self.assertNotEqual(a.tobytes(), b.tobytes())        # ...though its bytes differ
        with self.assertRaisesRegex(AssertionError, "non-finite"):
            assert_bits_equal(a, b, "the witness")

    def test_matched_nan_and_infinity_are_refused(self):
        for bad in (np.nan, np.inf, -np.inf):
            a = np.array([1.0, bad], np.float32)
            with self.assertRaisesRegex(AssertionError, "non-finite"):
                assert_bits_equal(a, a.copy(), f"matched {bad}")
            with self.assertRaisesRegex(AssertionError, "1 non-finite"):
                assert_finite(a, "rows")

    def test_signed_zero_and_one_ulp_are_refused(self):
        with self.assertRaisesRegex(AssertionError, "bit patterns differ"):
            assert_bits_equal(np.array([0.0, 1.0], np.float32), np.array([-0.0, 1.0], np.float32), "signed zero")
        x = np.array([1.5], np.float32)
        with self.assertRaisesRegex(AssertionError, "bit patterns differ"):
            assert_bits_equal(x, np.nextafter(x, np.float32(2.0)), "one ulp")

    def test_dtype_and_shape_are_checked(self):
        with self.assertRaisesRegex(AssertionError, "not float32"):
            assert_bits_equal(np.zeros(2), np.zeros(2), "float64")
        with self.assertRaisesRegex(AssertionError, "shapes"):
            assert_bits_equal(np.zeros(2, np.float32), np.zeros(3, np.float32), "shape")

    def test_identical_finite_bits_pass(self):
        a = np.random.default_rng(9).standard_normal((5, 3, 40)).astype(np.float32)
        a[0, 0, 0] = -0.0
        assert_bits_equal(a, a.copy(), "identical")
        assert_bits_equal(a[:, 1, :], a.copy()[:, 1, :], "one strided slot")


if __name__ == "__main__":
    unittest.main()
