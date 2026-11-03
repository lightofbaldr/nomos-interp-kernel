"""Filesystem witnesses use temporary roots only, and fake systemd observations."""
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("purge", Path(__file__).resolve().parents[2] / "runtime/files/purge.py")
purge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(purge)


class Workspaces(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glimmer-purge-witness-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "owned"
        for rel in ("", "workspaces", "locks", "workspaces/2", "workspaces/3"):
            (self.root / rel).mkdir(mode=0o700)
        for ident in ("2", "3"):
            (self.root / "locks" / ident).touch(mode=0o600)
            (self.root / "workspaces" / ident / "file").write_text(ident)
        self.request = {"all": True, "ids": ["2", "3"]}

    def run_purge(self, state=b"LoadState=not-found\nActiveState=inactive\n"):
        with patch.object(purge.subprocess, "run", return_value=types.SimpleNamespace(stdout=state)):
            purge.purge(str(self.root), self.request)

    def assert_retained(self):
        self.assertEqual((self.root / "workspaces/2/file").read_text(), "2")

    def test_positive_and_child_symlink(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir(); (outside / "secret").write_text("untouched")
        (self.root / "workspaces/2/outside").symlink_to(outside)
        self.run_purge()
        self.assertFalse((self.root / "workspaces/2").exists())
        self.assertFalse((self.root / "workspaces/3").exists())
        self.assertEqual((outside / "secret").read_text(), "untouched")

    def test_all_locks_admitted_before_first_removal(self):
        with (self.root / "locks/3").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_purge()
        self.assert_retained()

    def test_active_or_unknown_unit_refuses(self):
        for state in (b"LoadState=loaded\nActiveState=active\n", b""):
            with self.assertRaises(ValueError):
                self.run_purge(state)
            self.assert_retained()

    def test_symlinked_lock_and_later_workspace_refuse_before_first_removal(self):
        (self.root / "locks/3").unlink()
        (self.root / "locks/3").symlink_to(self.root / "locks/2")
        with self.assertRaises(OSError):
            self.run_purge()
        self.assert_retained()
        (self.root / "locks/3").unlink(); (self.root / "locks/3").touch(mode=0o600)
        (self.root / "workspaces/3/file").unlink(); (self.root / "workspaces/3").rmdir()
        (self.root / "workspaces/3").symlink_to(self.root / "workspaces/2")
        with self.assertRaises(OSError):
            self.run_purge()
        self.assert_retained()

    def test_unknown_directory_and_unsafe_inputs_refuse(self):
        (self.root / "workspaces/extra").mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.run_purge()
        self.assert_retained()
        for ids in (["../2"], ["0"], ["2", "2"], ["/tmp"], [2]):
            with self.assertRaises((ValueError, TypeError)):
                purge.purge(str(self.root), {"all": False, "ids": ids})
            self.assert_retained()


if __name__ == "__main__":
    unittest.main()
