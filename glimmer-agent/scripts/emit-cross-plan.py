"""CPU-only producer fixtures from the built Plan 2 serve, never its engine.

Prints JSON to stdout for review/publication; writes no checkout files. Run with
PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' and the local test interpreter.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
expected = "5e4a100c137bc01b42ba44c2cde473ad435711c0"
assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() == expected
assert not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip()
sys.path.insert(0, str(root / "pyhost"))
import test_readout_serve as producer
import readout

producer.NomosServe.setUpClass()
t = producer.NomosServe()
t.setUp()
t.serve.time.time = lambda: 1793040000  # freeze completion IDs/timestamps in the producer fixtures
cases = []

class MissingWorkspace(producer.FakeLM):
    def records(self, *args):
        count, raw = super().records(*args)
        recs = producer.np.frombuffer(raw, dtype=self.layout.dtype).copy()
        recs["flags"] &= readout.FLAG_EMO
        return count, recs.tobytes()

try:
    for spec in (False, True):
        for name, cached, strip, budget, missing in (
            ("all", 0, 0, None, False), ("reuse", 4, 0, None, False),
            ("stripped-bos", 0, 1, None, False), ("budget", 0, 0, 6, False),
            ("workspace-unavailable", 0, 0, None, True),
        ):
            block = copy.deepcopy(producer.NOMOS)
            block["readout"]["workspace"]["k"] = 2
            block["readout"]["prompt"] = "new" if cached or strip else "all"
            lm = (MissingWorkspace if missing else producer.FakeLM)(t.tok, cached=cached, strip=strip)
            req = t.req(block, **({"max_tokens": budget} if budget else {}))
            _, chunks = t.stream(producer.fake_engine(t.tok, lm, spec=spec), req)
            cases.append({"name": f'{"spec" if spec else "base"}-{name}', "block": block, "chunks": chunks})
    for name, block in (
        ("metadata-only", {"conversation": "17", "turn": 4}),
        ("steering-only", {"conversation": "17", "turn": 4, "steer": {
            "layer": 24, "directions": producer.DIRS, "direction": "calm", "strength": -0.7}}),
        ("emotion-only", {"conversation": "17", "turn": 4, "readout": {
            "emotion": {"layers": [20, 24, 29], "directions": producer.DIRS}, "prompt": "all"}}),
    ):
        _, chunks = t.stream(producer.fake_engine(t.tok, producer.FakeLM(t.tok), spec=True), t.req(block))
        cases.append({"name": name, "block": block, "chunks": chunks})
finally:
    t.tearDown()

files = ["pyhost/readout.py", "pyhost/serve.py", "pyhost/test_readout_serve.py"]
result_path = Path.home() / "nomos_data/readout-spine/e2e/result.json"
result_bytes = result_path.read_bytes()
print(json.dumps({"kernel_commit": expected,
                  "producer_sha256": {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files},
                  "real_e2e_sha256": hashlib.sha256(result_bytes).hexdigest(),
                  "real_e2e": json.loads(result_bytes), "cases": cases}, allow_nan=False, separators=(",", ":")))
