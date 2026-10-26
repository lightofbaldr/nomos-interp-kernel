"""Readout spine, host side (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, sections 1 and
3): the asset registry, the binary record the daemon and the serve share, the speculative turn's record timing, and the
serve's assembly of nomos.readout/1 records."""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np

V = "nomos.readout/1"
MAX_STRENGTH = 2.0

# Reply codes for the readout calls (KernelLM) and their opcodes (17-22). -1 stays "an opcode this daemon does not know"
# (wire.py), -99 "the engine raised".
NO_READOUT = -2         # this kernel has no readout exports
UNKNOWN_ASSET = -3      # an ID the registry does not list, or lists as the other kind
ALTERED = -4            # a file's sha256 no longer matches what the asset recorded
NO_LAYER = -5           # a requested layer the asset does not have
REFUSED = -6            # the kernel refused, or an asset does not fit the model (the message says which)
NO_DIRECTION = -7       # a direction name the set does not have
BAD_STEER = -8          # a non-finite strength or one outside [-MAX_STRENGTH, MAX_STRENGTH], or a negative start position
NOT_CONFIGURED = -9     # a read with readouts off


class ReadoutError(RuntimeError):
    """A readout request the host refuses; `code` is its reply code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def registry_path() -> str:
    return os.path.expanduser(os.environ.get("NOMOS_READOUT_REGISTRY", "~/nomos_data/readout-assets/registry.json"))


def sha256_file(path: str) -> str:
    """A file's sha256, computed every time; nothing is cached. A cache keyed by mtime could miss a rewrite within one
    timestamp tick. The daemon only loads assets when the configuration changes, which costs about a second for four
    lens layers."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


class AssetRegistry:
    """registry.json (spec section 3). An asset's ID is the sha256 of its manifest.json, which records each file's
    sha256, so changing any file changes the ID. Every load re-checks the manifest and the files it reads."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or registry_path()
        with open(self.path) as f:
            self.assets = {a["id"]: a for a in json.load(f)["assets"]}

    def get(self, asset_id: str, kind: str) -> dict:
        a = self.assets.get(asset_id)
        if a is None or a["kind"] != kind:
            raise ReadoutError(UNKNOWN_ASSET, f"no {kind} asset {asset_id!r} in {self.path}")
        return a

    def _manifest(self, a: dict) -> dict:
        path = os.path.join(a["dir"], "manifest.json")
        if sha256_file(path) != a["id"]:
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: its manifest changed on disk")
        with open(path) as f:
            return json.load(f)

    def _layer(self, a: dict, man: dict, layer: int) -> dict:
        entry = man["layers"].get(str(int(layer)))
        if entry is None:
            raise ReadoutError(NO_LAYER, f"{a['kind']} asset {a['id'][:16]} has no layer {layer}")
        return entry

    def _array(self, a: dict, entry: dict, shape: tuple) -> np.ndarray:
        path = os.path.join(a["dir"], entry["file"])
        if sha256_file(path) != entry["sha256"]:
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: {entry['file']} changed on disk")
        arr = np.fromfile(path, dtype=np.float32)
        if arr.size != int(np.prod(shape)):
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: {entry['file']} is not {shape}")
        return arr.reshape(shape)

    def directions(self, asset_id: str, layers) -> np.ndarray:
        """Unit directions [len(layers), E, D], float32."""
        a = self.get(asset_id, "directions")
        man = self._manifest(a)
        return np.ascontiguousarray(np.stack([self._array(a, self._layer(a, man, layer), (man["E"], man["D"]))
                                              for layer in layers]))

    def lens(self, asset_id: str, layers) -> tuple[np.ndarray, np.ndarray]:
        """The J-lens matrices [len(layers), D, D] (lens(h) = h @ J^T) and the final-norm weights [D], float32."""
        a = self.get(asset_id, "lens")
        man = self._manifest(a)
        mats = np.stack([self._array(a, self._layer(a, man, layer), (man["D"], man["D"])) for layer in layers])
        return np.ascontiguousarray(mats), np.ascontiguousarray(self._array(a, man["norm"], (man["D"],)))

    def steer_vector(self, asset_id: str, layer: int, name: str, strength: float) -> np.ndarray:
        """s x layer_sizes[layer] x the set's unit direction: sp1/steer.py's units. s is signed: a negative s steers away,
        like its minus- kinds (spec decision 5). Float32 [D]."""
        a = self.get(asset_id, "directions")
        if name not in a["names"]:
            raise ReadoutError(NO_DIRECTION, f"direction set {asset_id[:16]} has no direction {name!r}")
        s = float(strength)
        if not (math.isfinite(s) and abs(s) <= MAX_STRENGTH):
            raise ReadoutError(BAD_STEER, f"steering strength {strength!r} is outside [-{MAX_STRENGTH}, {MAX_STRENGTH}]")
        v = self.directions(asset_id, [layer])[0][a["names"].index(name)].astype(np.float64)
        return np.ascontiguousarray(s * float(a["layer_sizes"][int(layer)]) * v, dtype=np.float32)
