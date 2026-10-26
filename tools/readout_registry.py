"""Readout spine asset registry (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 3).
Scans <ROOT>/*/manifest.json and writes <ROOT>/registry.json. An asset's ID is its manifest's sha256. Run it after any
converter: pixi run --no-install python3 tools/readout_registry.py [ROOT]"""
import hashlib
import json
import os
import sys

ROOT = os.path.expanduser("~/nomos_data/readout-assets")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def entry(d):
    path = os.path.join(d, "manifest.json")
    with open(path) as f:
        man = json.load(f)
    e = {"id": sha(path), "kind": man["kind"], "dir": os.path.realpath(d), "D": man["D"],
         "layers": sorted(int(x) for x in man["layers"]), "source": man["source"], "source_sha256": man["source_sha256"]}
    if man["kind"] == "directions":
        e.update(E=man["E"], names=man["names"], layer_sizes=man["layer_sizes"])
    elif man["kind"] != "lens":
        raise SystemExit(f"{path}: unknown kind {man['kind']!r}")
    return e


def main(root=ROOT):
    assets = [entry(os.path.join(root, n)) for n in sorted(os.listdir(root))
              if os.path.isfile(os.path.join(root, n, "manifest.json"))]
    out = os.path.join(root, "registry.json")
    with open(out + ".tmp", "w") as f:
        json.dump({"v": 1, "assets": assets}, f, indent=1)
    os.replace(out + ".tmp", out)
    for a in assets:
        print(a["kind"], a["id"], os.path.basename(a["dir"]), f"{len(a['layers'])} layers")


if __name__ == "__main__":
    main(*sys.argv[1:])
