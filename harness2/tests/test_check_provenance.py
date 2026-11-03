"""scripts/check_provenance.py: notice metadata in ASSETS.json is not an asset; every pinned asset is still checked."""

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_provenance.py"


def check(root=None):
    spec = importlib.util.spec_from_file_location("check_provenance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with contextlib.redirect_stdout(io.StringIO()):
        return module.main() if root is None else module.main(root)


def release(root, extra):
    asset = root / "third_party" / "runner.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("pinned\n")
    manifest = {"third_party/runner.txt": hashlib.sha256(asset.read_bytes()).hexdigest(), **extra}
    (root / "dependencies").mkdir()
    (root / "dependencies" / "ASSETS.json").write_text(json.dumps(manifest))
    return asset


class CheckProvenanceTest(unittest.TestCase):
    def test_notice_metadata_is_not_an_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            release(Path(tmp), {"_notice": "a modification notice"})
            self.assertEqual(check(Path(tmp)), 0)

    def test_a_changed_asset_still_fails_beside_a_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            release(Path(tmp), {"_notice": "a modification notice"}).write_text("changed\n")
            self.assertEqual(check(Path(tmp)), 1)

    def test_the_release_tree_passes(self):
        self.assertEqual(check(), 0)


if __name__ == "__main__":
    unittest.main()
