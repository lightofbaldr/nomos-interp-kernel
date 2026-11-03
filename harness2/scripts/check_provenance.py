#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md.

"""Verify the release's runner and patch files against dependencies/ASSETS.json.

Keys starting with "_" are metadata (the file's modification notice), not assets.
"""

import hashlib
import json
from pathlib import Path


def main(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        manifest = json.loads((root / "dependencies/ASSETS.json").read_text())
        expected = {name: digest for name, digest in manifest.items() if not name.startswith("_")}
        failures = []
        for name, digest in expected.items():
            path = root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                failures.append(name)
    except (OSError, ValueError) as exc:
        print(f"Provenance check failed: {exc}")
        return 1
    if failures:
        print("Changed or missing release assets: " + ", ".join(failures))
        return 1
    print(f"PASS: {len(expected)} release asset checksums")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
