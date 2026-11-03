import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.substrate.login_tools_files import FILE_BYTES, Files


class FileTools(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-login-files-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("context", "workspace", "outside"):
            (self.root / name).mkdir()
        (self.root / "context/source.txt").write_text("readonly")
        self.files = Files(self.root / "context", self.root / "workspace")
        self.addCleanup(self.files.close)

    def test_read_write_list_and_context_is_read_only(self):
        self.assertEqual(self.files.read_file("context/source.txt")["content"], "readonly")
        written = self.files.write_file("workspace/harness/a.md", "candidate")
        self.assertEqual(
            self.files.read_file("workspace/harness/a.md")["sha256"], written["sha256"]
        )
        self.files.write_file("workspace/harness/a.md", "revised")
        self.assertEqual(self.files.list_dir("workspace")["entries"][0]["name"], "harness")
        with self.assertRaises(ValueError):
            self.files.write_file("context/source.txt", "no")
        self.assertEqual((self.root / "context/source.txt").read_text(), "readonly")

    def test_paths_refuse_before_touching_outside(self):
        (self.root / "outside/secret").write_text("sentinel")
        for path in (
            "../outside/secret",
            "workspace/../outside/secret",
            "/etc/passwd",
            "~/.env",
            "context//source.txt",
            "context/./source.txt",
            "https://x/",
            "workspace/" + "x/" * 33 + "file",
            "workspace/\x00x",
            "workspace\\x",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.files.read_file(path)
            with self.subTest(write=path), self.assertRaises(ValueError):
                self.files.write_file(path, "bad")
        self.assertEqual((self.root / "outside/secret").read_text(), "sentinel")

    def test_symlink_parent_leaf_hardlink_fifo_and_device_refused(self):
        work = self.root / "workspace"
        outside = self.root / "outside/secret"
        outside.write_text("untouched")
        (work / "linkdir").symlink_to(self.root / "outside")
        (work / "link").symlink_to(outside)
        os.link(outside, work / "hard")
        os.mkfifo(work / "pipe")
        (work / "device").symlink_to("/dev/null")
        for name in ("linkdir/secret", "link", "hard", "pipe", "device"):
            with self.subTest(read=name), self.assertRaises((OSError, ValueError)):
                self.files.read_file("workspace/" + name)
            with self.subTest(write=name), self.assertRaises((OSError, ValueError)):
                self.files.write_file("workspace/" + name, "bad")
        with self.assertRaises((OSError, ValueError)):
            self.files.list_dir("workspace")
        self.assertEqual(outside.read_text(), "untouched")
        from harness2.substrate.login_tools_files import regular

        with self.assertRaises(ValueError):
            regular(os.stat("/dev/null"))

    def test_size_total_and_entry_budgets(self):
        with self.assertRaises(ValueError):
            self.files.write_file("workspace/large", "x" * (FILE_BYTES + 1))
        (self.root / "workspace/large").write_bytes(b"x" * (FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.files.read_file("workspace/large")
        with patch("harness2.substrate.login_tools_files.MAX_BYTES", 8):
            self.files.write_file("workspace/a", "12345678")
            with self.assertRaises(ValueError):
                self.files.write_file("workspace/a", "x")
        with patch("harness2.substrate.login_tools_files.MAX_FILES", self.files.entries):
            with self.assertRaises(ValueError):
                self.files.write_file("workspace/new", "")

    def test_linked_root_and_link_in_initial_tree_refused(self):
        (self.root / "alias").symlink_to(self.root / "context")
        with self.assertRaises((OSError, ValueError)):
            Files(self.root / "alias", self.root / "workspace")
        (self.root / "workspace/hidden").symlink_to(self.root / "outside")
        with self.assertRaises((OSError, ValueError)):
            Files(self.root / "context", self.root / "workspace")

    def test_response_is_utf8_and_no_nul_writes(self):
        (self.root / "workspace/binary").write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            self.files.read_file("workspace/binary")
        with self.assertRaises(ValueError):
            self.files.write_file("workspace/nul", "a\0b")
