#!/usr/bin/env python3
"""Readout spine assets (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 3): the
direction converter, the registry builder and readout.AssetRegistry, on synthetic assets. CPU only.
Run: pixi run --no-install python3 pyhost/test_readout_assets.py -v"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import readout  # noqa: E402
import readout_convert_directions  # noqa: E402
import readout_registry  # noqa: E402

D, E, L = 8, 3, 4
NAMES = ["calm", "joyful", "afraid"]


def make_vectors(path, seed=0):
    """A vectors.npz shaped like SP1's: v [L, E, D], layer_sizes [L], emotions [E]."""
    rng = np.random.default_rng(seed)
    v = (rng.standard_normal((L, E, D)) * 5).astype(np.float32)
    sizes = np.linspace(60, 90, L).astype(np.float32)
    np.savez(path, v=v, layer_sizes=sizes, emotions=np.array(NAMES))
    return v, sizes


def make_lens(out, layers, seed=1):
    """A lens asset in tools/readout_convert_lens.py's format."""
    os.makedirs(out)
    rng = np.random.default_rng(seed)
    man = {"kind": "lens", "source": "synthetic.pt", "source_sha256": "0" * 64,
           "convention": "lens(h) = h @ J^T; J row-major [D, D] float32", "D": D, "layers": {}}
    for layer in layers:
        name = f"J_L{layer}.f32"
        rng.standard_normal((D, D)).astype(np.float32).tofile(os.path.join(out, name))
        man["layers"][str(layer)] = {"file": name, "sha256": readout.sha256_file(os.path.join(out, name)),
                                     "shape": [D, D]}
    np.ones(D, np.float32).tofile(os.path.join(out, "norm_weight.f32"))
    man["norm"] = {"file": "norm_weight.f32", "sha256": readout.sha256_file(os.path.join(out, "norm_weight.f32")),
                   "source": "norm_weight.bin", "source_sha256": "0" * 64}
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)


def build_assets(root):
    """Synthetic directions (layers 0-3) and lens (layers 1, 3) assets under root/assets, with their registry. Returns
    (v, layer_sizes, registry path)."""
    v, sizes = make_vectors(os.path.join(root, "vectors.npz"))
    with contextlib.redirect_stdout(io.StringIO()):
        readout_convert_directions.main(os.path.join(root, "vectors.npz"), os.path.join(root, "assets", "directions"))
        make_lens(os.path.join(root, "assets", "lens"), [1, 3])
        readout_registry.main(os.path.join(root, "assets"))
    return v, sizes, os.path.join(root, "assets", "registry.json")


def ids_by_kind(reg):
    return (next(i for i, a in reg.assets.items() if a["kind"] == "directions"),
            next(i for i, a in reg.assets.items() if a["kind"] == "lens"))


class Assets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v, self.sizes, path = build_assets(self.tmp.name)
        self.reg = readout.AssetRegistry(path)
        self.dirs_id, self.lens_id = ids_by_kind(self.reg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_registry_lists_both_assets_by_their_manifest_hash(self):
        for asset_id, a in self.reg.assets.items():
            self.assertEqual(asset_id, readout.sha256_file(os.path.join(a["dir"], "manifest.json")))
        d = self.reg.assets[self.dirs_id]
        self.assertEqual((d["E"], d["D"], d["layers"], d["names"]), (E, D, [0, 1, 2, 3], NAMES))
        self.assertEqual(d["layer_sizes"], [float(x) for x in self.sizes])
        self.assertEqual(self.reg.assets[self.lens_id]["layers"], [1, 3])

    def test_directions_are_the_sources_rows_made_unit(self):
        got = self.reg.directions(self.dirs_id, [2, 0])
        self.assertEqual(got.shape, (2, E, D))
        np.testing.assert_allclose(np.linalg.norm(got.astype(np.float64), axis=-1), 1.0, atol=1e-6)
        v = self.v[2].astype(np.float64)
        np.testing.assert_allclose(got[0], v / np.linalg.norm(v, axis=-1, keepdims=True), atol=1e-7)

    def test_lens_returns_the_layers_asked_for_and_the_norm(self):
        J, norm = self.reg.lens(self.lens_id, [3])
        self.assertEqual((J.shape, norm.shape), ((1, D, D), (D,)))

    def test_the_steering_vector_has_sp1_units(self):
        got = self.reg.steer_vector(self.dirs_id, 1, "joyful", 0.7).astype(np.float64)
        v = self.v[1, 1].astype(np.float64)
        np.testing.assert_allclose(got, 0.7 * float(self.sizes[1]) * v / np.linalg.norm(v), rtol=1e-6)

    def test_a_negative_strength_steers_away(self):
        """Signed strength (spec decision 5): -s gives exactly the negated vector, like sp1/steer.py's minus- kinds."""
        away = self.reg.steer_vector(self.dirs_id, 1, "joyful", -0.7)
        toward = self.reg.steer_vector(self.dirs_id, 1, "joyful", 0.7)
        np.testing.assert_array_equal(away, -toward)

    def test_refusals_carry_their_codes(self):
        cases = [
            (lambda: self.reg.directions("f" * 64, [0]), readout.UNKNOWN_ASSET),
            (lambda: self.reg.directions(self.lens_id, [1]), readout.UNKNOWN_ASSET),     # an ID of the other kind
            (lambda: self.reg.lens(self.lens_id, [2]), readout.NO_LAYER),
            (lambda: self.reg.steer_vector(self.dirs_id, 0, "bored", 0.5), readout.NO_DIRECTION),
            (lambda: self.reg.steer_vector(self.dirs_id, 0, "calm", 2.5), readout.BAD_STEER),
            (lambda: self.reg.steer_vector(self.dirs_id, 0, "calm", float("nan")), readout.BAD_STEER),
            (lambda: self.reg.steer_vector(self.dirs_id, 0, "calm", -2.5), readout.BAD_STEER),
        ]
        for call, code in cases:
            with self.assertRaises(readout.ReadoutError) as cm:
                call()
            self.assertEqual(cm.exception.code, code)

    def test_a_file_altered_after_registration_is_refused(self):
        path = os.path.join(self.reg.assets[self.lens_id]["dir"], "J_L3.f32")
        data = np.fromfile(path, np.float32)
        data[0] += 1.0
        data.tofile(path)
        with self.assertRaises(readout.ReadoutError) as cm:
            self.reg.lens(self.lens_id, [3])
        self.assertEqual(cm.exception.code, readout.ALTERED)

    def test_a_manifest_altered_after_registration_is_refused(self):
        with open(os.path.join(self.reg.assets[self.dirs_id]["dir"], "manifest.json"), "a") as f:
            f.write("\n")
        with self.assertRaises(readout.ReadoutError) as cm:
            self.reg.directions(self.dirs_id, [0])
        self.assertEqual(cm.exception.code, readout.ALTERED)


if __name__ == "__main__":
    unittest.main()
