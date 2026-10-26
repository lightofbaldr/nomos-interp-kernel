"""Readout spine assets (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md). Writes the Glimmer
Jacobian lens (ck["J"][L], [D, D], lens(h) = h @ J^T) as per-layer float32 raw files, plus the final-norm weights,
all with sha256s. Run: pixi run --no-install python3 tools/readout_convert_lens.py 36 40 44 48"""
import hashlib
import json
import os
import sys

import numpy as np
import torch

SRC = "/home/adam/Projects/jspace-readout/lenses/glimmer/Muse-Glimmer-30B_jacobian_lens.pt"
NORM = "/home/adam/nomos_data/muse-glimmer-flat-nvfp4/norm_weight.bin"
OUT = os.path.expanduser("~/nomos_data/readout-assets/lens-muse-glimmer")
D = 6656


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def write(path, arr):
    tmp = path + ".tmp"
    arr.tofile(tmp)
    os.replace(tmp, path)


def main(layers):
    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "manifest.json")
    src_sha = sha(SRC)
    man = json.load(open(man_path)) if os.path.exists(man_path) else {
        "kind": "lens", "source": SRC, "source_sha256": src_sha,
        "convention": "lens(h) = h @ J^T; J row-major [D, D] float32", "D": D, "layers": {}}
    if man["source_sha256"] != src_sha:
        raise SystemExit("the lens source changed since this directory was written: use a new directory")
    ck = torch.load(SRC, map_location="cpu", weights_only=True)
    for layer in layers:
        J = ck["J"][layer].float().numpy()
        if J.shape != (D, D) or not np.isfinite(J).all():
            raise SystemExit(f"layer {layer}: shape {J.shape} or non-finite values")
        name = f"J_L{layer}.f32"
        write(os.path.join(OUT, name), np.ascontiguousarray(J, np.float32))
        man["layers"][str(layer)] = {"file": name, "sha256": sha(os.path.join(OUT, name)), "shape": [D, D]}
    norm = np.fromfile(NORM, np.float32)
    if norm.shape != (D,) or not np.isfinite(norm).all():
        raise SystemExit("the final-norm weights are not D finite floats")
    write(os.path.join(OUT, "norm_weight.f32"), norm)
    man["norm"] = {"file": "norm_weight.f32", "sha256": sha(os.path.join(OUT, "norm_weight.f32")),
                   "source": NORM, "source_sha256": sha(NORM)}
    tmp = man_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(man, fh, indent=1)
    os.replace(tmp, man_path)
    print(json.dumps({str(layer): man["layers"][str(layer)]["sha256"][:16] for layer in layers}))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
