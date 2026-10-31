"""API-key-only admission controls. No real credential, provider or toolchain."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

KEY = "FAKE-OPERATOR-API-KEY-NOT-A-REAL-CREDENTIAL"


class ApiKeyOnly(unittest.TestCase):
    def test_parent_key_and_login_are_not_a_credential_source(self):
        from harness2.substrate.improver_auth import ApiCredential

        with patch.object(Path, "open", side_effect=AssertionError("no credential file")):
            for env in (
                {},
                {"ANTHROPIC_API_KEY": KEY},
                {"CLAUDE_CODE_OAUTH_TOKEN": "FAKE-UNSUPPORTED-TOKEN"},
            ):
                with self.assertRaises(ValueError):
                    ApiCredential.from_env(env)

    def test_auth_values_are_validated_without_echoing_them(self):
        from harness2.substrate.improver_auth import ApiCredential

        for val in (None, "", 1, True, KEY + "\n", KEY + "é", "x" * 4097):
            with self.subTest(kind=type(val).__name__), self.assertRaises(ValueError) as err:
                ApiCredential(val)
            self.assertNotIn(KEY, str(err.exception))

    def test_default_off_and_explicit_api_key_are_the_only_adapter_options(self):
        from harness2.benchmarks.mojo import MojoAdapter

        with patch.dict(os.environ, {}, clear=True):
            adapter = MojoAdapter(corpus=Mock(identity={}))
            self.assertEqual(adapter.spec.channels["proposer"].sandbox, "host")
            self.assertEqual(adapter.identity["improver_auth"], "off")
        with patch.dict(os.environ, {"HARNESS2_MOJO_IMPROVER_AUTH": "api-key"}, clear=True):
            adapter = MojoAdapter(corpus=Mock(identity={}))
            for spec in adapter.spec.channels.values():
                self.assertEqual(spec.sandbox, "mojo-claude")
                self.assertEqual(spec.auth_mode, "api-key")
            self.assertEqual(adapter.identity["improver_auth"], "api-key")
        for mode in ("subscription", "login", "auto", ""):
            with (
                patch.dict(os.environ, {"HARNESS2_MOJO_IMPROVER_AUTH": mode}, clear=True),
                self.subTest(mode=mode),
                self.assertRaises(ValueError),
            ):
                MojoAdapter(corpus=Mock(identity={}))

    def test_channel_refuses_removed_or_ambiguous_modes(self):
        from harness2.core.spec import ChannelSpec

        for kw in (
            {"auth_mode": "api-key"},
            {"sandbox": "mojo-claude"},
            {"sandbox": "mojo-claude", "auth_mode": "subscription"},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                ChannelSpec("claude_cli", "opus", **kw)

    def test_client_is_bare_and_only_placeholder_enters_namespace(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import ImproverSandbox, prepare_client
        from harness2.substrate.improver_auth import PLACEHOLDER

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controls = root / "controls"
            controls.mkdir()
            spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
            command = prepare_client(controls, spec, "mission", "system")
            sandbox = ImproverSandbox(claude=Path(sys.executable), controls=controls)
            with patch.dict(
                os.environ,
                {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CODE_OAUTH_TOKEN": "FAKE-UNSUPPORTED-TOKEN"},
            ):
                args = sandbox.argv(root, command, broker=root / "fake.sock")
                identity = sandbox.identity()
            self.assertIn("--bare", command)
            self.assertIn(PLACEHOLDER, args)
            self.assertIn("ANTHROPIC_API_KEY", args)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", args)
            self.assertEqual(identity["authentication"], "api-key")
            self.assertNotIn(KEY, repr(args) + repr(identity))
            self.assertNotIn("FAKE-UNSUPPORTED-TOKEN", repr(args) + repr(identity))
            self.assertNotIn(KEY, (controls / "settings.json").read_text())

    def test_generic_executor_approval_is_not_improver_spend_approval(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import ImproverIdentityError, run_isolated

        spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
        for approval in (None, "", "0", "true"):
            env = {"HARNESS2_MOJO_LIVE_APPROVED": "1", "ANTHROPIC_API_KEY": KEY}
            if approval is not None:
                env["HARNESS2_MOJO_IMPROVER_API_APPROVED"] = approval
            with (
                patch.dict(os.environ, env, clear=True),
                self.subTest(approval=approval),
                patch(
                    "harness2.substrate.improver.ApiCredential.from_env",
                    side_effect=AssertionError("key read"),
                ),
                patch("harness2.substrate.improver.runtime", side_effect=AssertionError("runtime")),
                self.assertRaisesRegex(ImproverIdentityError, "API-key.*held"),
            ):
                run_isolated(Path("."), spec, "mission", "", 10)

    def test_no_supplied_key_still_refuses_after_explicit_approval(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import run_isolated

        with (
            patch.dict(
                os.environ,
                {"HARNESS2_MOJO_LIVE_APPROVED": "1", "HARNESS2_MOJO_IMPROVER_API_APPROVED": "1"},
                clear=True,
            ),
            patch("harness2.substrate.improver.config.CLAUDE_USE_VERTEX", False),
            patch("harness2.substrate.improver.runtime", side_effect=AssertionError("runtime")),
            self.assertRaisesRegex(ValueError, "credential"),
        ):
            run_isolated(
                Path("."),
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
                "mission",
                "",
                10,
            )

    def test_only_authorized_key_injection_reaches_fake_process(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import run_isolated
        from harness2.substrate.sandbox import ProcessResult

        sandbox = Mock()
        sandbox.identity.return_value = {"fake": "api-key"}
        sandbox.run.return_value = ProcessResult(
            0, '{"type":"result","subtype":"success","result":"fake"}', "", 1
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    "HARNESS2_MOJO_LIVE_APPROVED": "1",
                    "HARNESS2_MOJO_IMPROVER_API_APPROVED": "1",
                    "ANTHROPIC_API_KEY": "FAKE-PARENT-CLIENT-INTERNAL-KEY",
                    "HARNESS2_IMPROVER_API_KEY_FILE": str(Path(tmp) / "operator-key"),
                },
                clear=True,
            ),
            patch("harness2.substrate.improver.config.CLAUDE_USE_VERTEX", False),
            patch("harness2.substrate.improver.runtime", return_value=sandbox),
            patch("harness2.substrate.improver.ApiBroker") as broker,
        ):
            broker.return_value.__enter__.return_value = Mock(calls=0, last_error=None)
            key_file = Path(tmp) / "operator-key"
            key_file.write_text(KEY)
            key_file.chmod(0o600)
            work = Path(tmp) / "work"
            work.mkdir()
            reply = run_isolated(
                work,
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
                "mission",
                "",
                10,
            )
            self.assertTrue(reply.ok)
            self.assertEqual(broker.call_args.kwargs["api_key"], KEY)
            self.assertNotIn("mode", broker.call_args.kwargs)
            self.assertNotIn("input_data", sandbox.run.call_args.kwargs)
            self.assertNotIn(KEY, repr(sandbox.run.call_args) + json.dumps(reply.transcript))
            self.assertNotIn(
                str(key_file), repr(sandbox.run.call_args) + json.dumps(reply.transcript)
            )

    def test_literal_echo_is_redacted_and_attempt_fails(self):
        from harness2.substrate.improver_auth import ApiCredential
        from harness2.substrate.sandbox import ProcessResult

        result = ApiCredential(KEY).scrub(ProcessResult(0, "raw " + KEY, "err " + KEY, 1))
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertEqual(result.reason, "credential_echo")

    def test_removed_sources_and_subscription_switch_have_no_implementation(self):
        from harness2.substrate import improver, improver_broker

        self.assertFalse(hasattr(improver_broker, "Login"))
        self.assertFalse(hasattr(improver_broker, "load_login"))
        self.assertFalse(hasattr(improver, "SUBSCRIPTION_TOOLS_REVIEWED"))
        root = Path(__file__).resolve().parents[1] / "harness2/substrate"
        for path in root.glob("improver*.py"):
            code = path.read_text()
            for removed in (".credentials.json", "claudeAiOauth", "CLAUDE_CODE_OAUTH_TOKEN"):
                self.assertNotIn(removed, code, path)
