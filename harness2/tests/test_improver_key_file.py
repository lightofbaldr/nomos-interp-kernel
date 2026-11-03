"""Dedicated operator key source; only private temporary fake credentials."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.substrate.improver_auth import ApiCredential

SOURCE = "HARNESS2_IMPROVER_API_KEY_FILE"
KEY = "FAKE-ADAM-PROVISIONED-IMPROVER-KEY"
INHERITED = "FAKE-PARENT-CLAUDE-INTERNAL-KEY"


class DedicatedFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-key-file-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "operator-key"

    def key_file(self, data=KEY.encode(), mode=0o600):
        if self.path.exists():
            self.path.chmod(0o600)
        self.path.write_bytes(data)
        self.path.chmod(mode)
        return self.path

    def load(self, source=None):
        return ApiCredential.from_env(
            {SOURCE: str(source or self.path), "ANTHROPIC_API_KEY": INHERITED}
        )

    def test_owned_0600_file_is_the_only_key_even_with_parent_key_set(self):
        self.key_file()
        auth = self.load()
        self.assertEqual(auth.secret, KEY)
        self.assertNotIn(KEY, repr(auth))
        self.assertNotIn(INHERITED, repr(auth))

    def test_dedicated_variable_is_the_only_environment_lookup(self):
        self.key_file()
        path = str(self.path)

        class DedicatedOnly(dict):
            def get(self, key, default=None):
                if key != SOURCE:
                    raise AssertionError("inherited credential was inspected")
                return path

        self.assertEqual(ApiCredential.from_env(DedicatedOnly()).secret, KEY)

    def test_inherited_key_without_dedicated_source_never_reaches_broker_or_process(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import run_isolated

        spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
        with (
            patch.dict(
                os.environ,
                {
                    "HARNESS2_MOJO_LIVE_APPROVED": "1",
                    "HARNESS2_MOJO_IMPROVER_API_APPROVED": "1",
                    "ANTHROPIC_API_KEY": INHERITED,
                },
                clear=True,
            ),
            patch("harness2.substrate.improver.config.CLAUDE_USE_VERTEX", False),
            patch(
                "harness2.substrate.improver.runtime", side_effect=AssertionError("spawn")
            ) as runtime,
            patch(
                "harness2.substrate.improver.ApiBroker", side_effect=AssertionError("key sent")
            ) as broker,
            self.assertRaisesRegex(ValueError, SOURCE),
        ):
            run_isolated(self.root, spec, "fake mission", "", 10)
        runtime.assert_not_called()
        broker.assert_not_called()

    def test_missing_empty_relative_and_missing_file_refuse_without_fallback(self):
        for value in (None, "", "operator-key", "~/operator-key", str(self.root / "missing")):
            with self.subTest(source=value):
                with self.assertRaises(ValueError) as err:
                    ApiCredential.from_env({SOURCE: value, "ANTHROPIC_API_KEY": INHERITED})
                self.assertNotIn(INHERITED, str(err.exception))

    def test_only_exact_0600_permissions_are_accepted(self):
        for mode in (0o644, 0o640, 0o666, 0o400, 0o700, 0o4600):
            self.key_file(mode=mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.load()

    def test_wrong_owner_is_rejected(self):
        self.key_file()
        with patch("os.geteuid", return_value=os.geteuid() + 1), self.assertRaises(ValueError):
            self.load()

    def test_final_symlink_is_rejected(self):
        self.key_file()
        link = self.root / "linked-key"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            self.load(link)

    def test_symlinked_parent_is_rejected(self):
        self.key_file()
        link = self.root / "linked-directory"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.load(link / self.path.name)

    def test_hardlinked_file_is_rejected(self):
        self.key_file()
        os.link(self.path, self.root / "hardlink")
        with self.assertRaises(ValueError):
            self.load()

    def test_directory_and_fifo_refuse_without_reading(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        for path in (self.root, fifo):
            with self.subTest(kind=path.name), self.assertRaises(ValueError):
                self.load(path)

    def test_bounded_ascii_key_allows_one_terminal_newline_only(self):
        for data in (KEY.encode(), (KEY + "\n").encode(), (KEY + "\r\n").encode()):
            self.key_file(data)
            self.assertEqual(self.load().secret, KEY)
        for data in (
            b"",
            b"short",
            b"x" * 4097,
            b"\xff" * 32,
            (KEY + "\n\n").encode(),
            (KEY + " ").encode(),
            (KEY + "\nsecond-key").encode(),
            (KEY + "\0").encode(),
        ):
            self.key_file(data)
            with self.subTest(size=len(data)), self.assertRaises(ValueError) as err:
                self.load()
            self.assertNotIn(KEY, str(err.exception))
            self.assertNotIn(INHERITED, str(err.exception))

    def test_replaced_mode_during_read_refuses(self):
        self.key_file()
        original = os.fstat
        calls = 0

        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.path.chmod(0o644)
            return original(fd)

        with patch("os.fstat", side_effect=changed), self.assertRaises(ValueError):
            self.load()
