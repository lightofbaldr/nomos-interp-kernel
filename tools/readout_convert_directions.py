"""Readout spine direction sets (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 3).
SP1's vectors.npz (v [L, E, D], layer_sizes [L], emotions [E]) becomes one file of unit directions [E, D] float32 per
layer, normalized in float64, plus a manifest with the names and layer_sizes (steering's units, sp1/steer.py).
Run: pixi run --no-install python3 tools/readout_convert_directions.py [SRC [OUT]]"""
import hashlib
import json
import os
import sys

import numpy as np

SRC = os.path.expanduser("~/nomos_data/emotions/glimmer/sp1/vectors/vectors.npz")
OUT = os.path.expanduser("~/nomos_data/readout-assets/directions-sp1")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def main(src=SRC, out=OUT):
    os.makedirs(out, exist_ok=True)
    d = np.load(src)
    v, sizes, names = d["v"], d["layer_sizes"], [str(x) for x in d["emotions"]]
    L, E, D = v.shape
    if sizes.shape != (L,) or len(names) != E or not np.isfinite(v).all() or not np.isfinite(sizes).all():
        raise SystemExit(f"{src}: v {v.shape}, layer_sizes {sizes.shape}, {len(names)} names, or non-finite values")
    man = {"kind": "directions", "source": src, "source_sha256": sha(src), "D": int(D), "E": int(E), "names": names,
           "layer_sizes": [float(x) for x in sizes], "layers": {}}
    for layer in range(L):
        u = v[layer].astype(np.float64)
        n = np.linalg.norm(u, axis=1, keepdims=True)
        if not (n > 0).all():
            raise SystemExit(f"layer {layer}: a zero direction")
        u32 = np.ascontiguousarray(u / n, dtype=np.float32)
        if np.abs(np.linalg.norm(u32.astype(np.float64), axis=1) - 1.0).max() > 1e-6:
            raise SystemExit(f"layer {layer}: float32 rounding moved a norm by more than 1e-6")
        name = f"dirs_L{layer}.f32"
        path = os.path.join(out, name)
        u32.tofile(path + ".tmp")
        os.replace(path + ".tmp", path)
        man["layers"][str(layer)] = {"file": name, "sha256": sha(path), "shape": [int(E), int(D)]}
    path = os.path.join(out, "manifest.json")
    with open(path + ".tmp", "w") as f:
        json.dump(man, f, indent=1)
    os.replace(path + ".tmp", path)
    print(f"{out}: {L} layers x {E} directions x {D}, manifest {sha(path)[:16]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
