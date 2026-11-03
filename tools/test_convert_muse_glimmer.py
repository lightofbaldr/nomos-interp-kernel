#!/usr/bin/env python3
"""tools/convert_muse_glimmer.py: its arguments, and the untied head the W4A4 loader needs. CPU only, a few seconds.

What is pinned:
- `--format nvfp4` and `--format=nvfp4` parse the same; a missing, extra or unknown argument is refused before the
  output directory exists (the old parser took the space spelling's `nvfp4` as the input directory);
- an nvfp4 conversion writes the head as lm_head_weight.nvfp4, encoded from lm_head (not the embedding) with the
  existing encoder, because the Muse profile's W4A4 loader reads the head from there and fails at init without it;
  a q4 conversion writes no .nvfp4 head;
- a head equal to the embedding is refused.
The end-to-end case runs main() on a tiny synthetic checkpoint with Muse's tensor names and checks the whole output
inventory. Needs numpy, safetensors and torch (the pixi environment has them).
Run: python3 tools/test_convert_muse_glimmer.py -v"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

import numpy as np

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import convert_muse_glimmer as conv  # noqa: E402
from quantize_nvfp4 import write_nvfp4  # noqa: E402


def encoded(W):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "w.nvfp4")
        write_nvfp4(path, W)
        return pathlib.Path(path).read_bytes()


def tmpdir(test):
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d)
    return d


class Arguments(unittest.TestCase):
    def test_both_format_spellings_parse_the_same(self):
        self.assertEqual(conv.parse_args(["--format", "nvfp4", "IN", "OUT"]), ("IN", "OUT", "nvfp4"))
        self.assertEqual(conv.parse_args(["--format=nvfp4", "IN", "OUT"]), ("IN", "OUT", "nvfp4"))

    def test_the_default_format_is_q4(self):
        self.assertEqual(conv.parse_args(["IN", "OUT"]), ("IN", "OUT", "q4"))

    def test_bad_arguments_are_refused_before_any_output(self):
        for argv in ([], ["IN"], ["IN", "OUT", "EXTRA"], ["--format", "bf16", "IN", "OUT"],
                     ["--format", "IN", "OUT"], ["--format"]):
            with self.subTest(argv=argv):
                out = os.path.join(tmpdir(self), "out")
                argv = [out if a == "OUT" else a for a in argv]
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    conv.main(argv)
                self.assertFalse(os.path.exists(out))


class Head(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.emb = rng.standard_normal((64, 32)).astype(np.float32)
        self.lm = (3 * rng.standard_normal((64, 32))).astype(np.float32)

    def test_nvfp4_head_is_encoded_from_lm_head(self):
        d = tmpdir(self)
        conv.write_embed_and_head(d, self.emb, self.lm)
        entry = conv.write_head_nvfp4(d, self.lm, write_nvfp4)
        self.assertEqual(sorted(os.listdir(d)),
                         ["embed_tokens_weight.bin", "lm_head_weight.bin", "lm_head_weight.nvfp4"])
        head = pathlib.Path(d, "lm_head_weight.nvfp4").read_bytes()
        self.assertEqual(head, encoded(self.lm))
        self.assertNotEqual(head, encoded(self.emb))
        self.assertEqual(entry, {"nvfp4": "lm_head_weight.nvfp4"})

    def test_a_head_equal_to_the_embedding_is_refused(self):
        with self.assertRaises(SystemExit):
            conv.write_embed_and_head(tmpdir(self), self.emb, self.emb.copy())


class EndToEnd(unittest.TestCase):
    """main() on a tiny checkpoint with every tensor name the converter reads (N_LAYERS layers, 16x32 Linears)."""

    def checkpoint(self):
        from safetensors.numpy import save_file
        rng = np.random.default_rng(11)
        t = {}
        for i in range(conv.N_LAYERS):
            pre = f"model.language_model.layers.{i}."
            for suf in conv.LINEARS:
                t[pre + suf + ".weight"] = rng.standard_normal((16, 32)).astype(np.float32)
            for nm in conv.CENTERED_NORMS:
                t[pre + nm + ".weight"] = rng.standard_normal(32).astype(np.float32)
        t["model.language_model.embed_tokens.weight"] = rng.standard_normal((48, 32)).astype(np.float32)
        t["lm_head.weight"] = (2 * rng.standard_normal((48, 32))).astype(np.float32)
        t["model.language_model.norm.weight"] = rng.standard_normal(32).astype(np.float32)
        src = tmpdir(self)
        save_file(t, os.path.join(src, "model.safetensors"))
        with open(os.path.join(src, "model.safetensors.index.json"), "w") as f:
            json.dump({"weight_map": {k: "model.safetensors" for k in t}}, f)
        return src, t

    def convert(self, fmt):
        src, t = self.checkpoint()
        out = os.path.join(tmpdir(self), "out")
        with contextlib.redirect_stdout(io.StringIO()):
            conv.main(["--format", fmt, src, out])
        return out, t

    def test_nvfp4_inventory_includes_the_head_the_loader_needs(self):
        out, t = self.convert("nvfp4")
        names = set(os.listdir(out))
        per_layer = {conv._stem(f"model.language_model.layers.{i}.{s}.weight") + ".nvfp4"
                     for i in range(conv.N_LAYERS) for s in conv.LINEARS}
        norms = {conv._stem(f"model.language_model.layers.{i}.{n}.weight") + ".bin"
                 for i in range(conv.N_LAYERS) for n in conv.CENTERED_NORMS}
        rest = {"embed_tokens_weight.bin", "lm_head_weight.bin", "lm_head_weight.nvfp4", "norm_weight.bin",
                "convert_manifest.json"}
        self.assertEqual(names, per_layer | norms | rest)
        self.assertEqual(pathlib.Path(out, "lm_head_weight.nvfp4").read_bytes(), encoded(t["lm_head.weight"]))
        with open(os.path.join(out, "convert_manifest.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["untied_lm_head"]["nvfp4"], "lm_head_weight.nvfp4")

    def test_q4_inventory_has_no_nvfp4_head(self):
        out, _ = self.convert("q4")
        self.assertNotIn("lm_head_weight.nvfp4", os.listdir(out))
        self.assertIn("lm_head_weight.bin", os.listdir(out))


if __name__ == "__main__":
    unittest.main()
