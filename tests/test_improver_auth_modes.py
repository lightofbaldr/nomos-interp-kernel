"""No real credential, login, provider or toolchain in these controls."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

TOKEN = "FAKE-SETUP-TOKEN-ONLY-NOT-A-REAL-CREDENTIAL"
KEY = "FAKE-OPERATOR-API-KEY-NOT-A-REAL-CREDENTIAL"


class AuthModes(unittest.TestCase):
    def test_only_explicit_operator_environment_is_read(self):
        from harness2.substrate.improver_auth import Credentials

        with patch.object(Path, "open", side_effect=AssertionError("no credential file")):
            a = Credentials.from_env("subscription", {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
            b = Credentials.from_env("api-key", {"ANTHROPIC_API_KEY": KEY})
            self.assertEqual(a.client_input(), TOKEN.encode())
            self.assertIsNone(a.broker_key())
            self.assertEqual(b.broker_key(), KEY)
            self.assertNotIn(KEY.encode(), b.client_input())
            self.assertNotIn(TOKEN, repr(a))
            self.assertNotIn(KEY, repr(b))
            for mode, env in (("off", {}), ("typo", {}), ("subscription", {}), ("api-key", {})):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    Credentials.from_env(mode, env)

    def test_auth_values_are_validated_without_echoing_them(self):
        from harness2.substrate.improver_auth import Credentials

        for val in (None, "", 1, True, TOKEN + "\n", TOKEN + "é", "x" * 4097):
            with self.subTest(kind=type(val).__name__), self.assertRaises(ValueError) as err:
                Credentials.from_env("subscription", {"CLAUDE_CODE_OAUTH_TOKEN": val})
            self.assertNotIn(TOKEN, str(err.exception))

    def test_default_off_is_not_a_sandbox_failure_fallback(self):
        from harness2.benchmarks.mojo import MojoAdapter

        corpus = Mock(identity={})
        with patch.dict(os.environ, {}, clear=True):
            adapter = MojoAdapter(corpus=corpus)
            self.assertEqual(adapter.spec.channels["proposer"].sandbox, "host")
            self.assertEqual(adapter.identity["improver_auth"], "off")
        for mode in ("subscription", "api-key"):
            with patch.dict(os.environ, {"HARNESS2_MOJO_IMPROVER_AUTH": mode}, clear=True):
                adapter = MojoAdapter(corpus=corpus)
                for spec in adapter.spec.channels.values():
                    self.assertEqual(spec.sandbox, "mojo-claude")
                    self.assertEqual(spec.auth_mode, mode)
                self.assertEqual(adapter.identity["improver_auth"], mode)
        with patch.dict(os.environ, {"HARNESS2_MOJO_IMPROVER_AUTH": "auto"}, clear=True):
            with self.assertRaises(ValueError):
                MojoAdapter(corpus=corpus)

    def test_channel_identity_refuses_ambiguous_modes(self):
        from harness2.core.spec import ChannelSpec

        for kw in (
            {"auth_mode": "api-key"},
            {"sandbox": "mojo-claude"},
            {"sandbox": "mojo-claude", "auth_mode": "login"},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                ChannelSpec("claude_cli", "opus", **kw)

    def test_client_mode_is_flags_for_subscription_and_bare_for_key(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import prepare_client

        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("subscription", "api-key"):
                spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode=mode)
                command = prepare_client(Path(tmp), spec, "mission", "system")
                self.assertEqual("--bare" in command, mode == "api-key")
                self.assertNotIn(TOKEN, json.dumps(command))
                self.assertNotIn(KEY, json.dumps(command))

    def test_entry_receives_token_only_through_stdin_then_child_environment(self):
        from harness2.substrate.improver_entry import client_env

        for mode, token, key in (
            ("subscription", TOKEN, "CLAUDE_CODE_OAUTH_TOKEN"),
            ("api-key", "harness2-inert-api-placeholder", "ANTHROPIC_API_KEY"),
        ):
            env = client_env(mode, io.BytesIO(token.encode()))
            self.assertEqual(env[key], token)
            other = "ANTHROPIC_API_KEY" if mode == "subscription" else "CLAUDE_CODE_OAUTH_TOKEN"
            self.assertNotIn(other, env)
        with self.assertRaises(ValueError):
            client_env("subscription", io.BytesIO(b""))

    def test_transcript_scrub_is_literal_not_a_claim_about_model_authored_files(self):
        from harness2.substrate.improver_auth import Credentials
        from harness2.substrate.sandbox import ProcessResult

        auth = Credentials.from_env("subscription", {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
        result = auth.scrub(ProcessResult(0, "raw " + TOKEN, "err " + TOKEN, 1))
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertEqual(result.reason, "credential_echo")

    def test_subscription_live_is_held_before_reading_any_credential(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import ImproverIdentityError, run_isolated

        with (
            patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
            patch("harness2.substrate.improver.Credentials.from_env", side_effect=AssertionError),
            self.assertRaisesRegex(ImproverIdentityError, "subscription.*hold"),
        ):
            run_isolated(
                Path("."),
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="subscription"),
                "mission",
                "",
                10,
            )

    def test_legacy_login_reader_has_no_code_or_tests(self):
        from harness2.substrate import improver_broker

        self.assertFalse(hasattr(improver_broker, "load_login"))
        self.assertFalse(hasattr(improver_broker, "Login"))
        root = Path(__file__).resolve().parents[1] / "harness2/substrate"
        for path in root.glob("improver*.py"):
            code = path.read_text()
            self.assertNotIn(".credentials.json", code, path)
            self.assertNotIn("claudeAiOauth", code, path)

    def test_subscription_orchestration_injects_only_into_client_pipe(self):
        from harness2.core.spec import ChannelSpec
        from harness2.substrate.improver import run_isolated
        from harness2.substrate.sandbox import ProcessResult

        sandbox = Mock()
        sandbox.identity.return_value = {"fake": "subscription"}
        sandbox.run.return_value = ProcessResult(
            0, '{"type":"result","subtype":"success","result":"fake only"}', "", 1
        )
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "HARNESS2_MOJO_LIVE_APPROVED": "1",
                    "CLAUDE_CODE_OAUTH_TOKEN": TOKEN,
                    "ANTHROPIC_API_KEY": KEY,
                },
            ),
            # A counterfactual fake only: production hold has no env override.
            patch("harness2.substrate.improver.SUBSCRIPTION_TOOLS_REVIEWED", True),
            patch("harness2.substrate.improver.config.CLAUDE_USE_VERTEX", False),
            patch("harness2.substrate.improver.runtime", return_value=sandbox) as runtime,
            patch("harness2.substrate.improver.ApiBroker") as broker,
        ):
            broker.return_value.__enter__.return_value = Mock(calls=0, last_error=None)
            reply = run_isolated(
                Path(temp),
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="subscription"),
                "mission",
                "",
                30,
            )
            self.assertTrue(reply.ok)
            self.assertEqual(runtime.call_args.kwargs["auth_mode"], "subscription")
            self.assertEqual(broker.call_args.kwargs["mode"], "subscription")
            self.assertIsNone(broker.call_args.kwargs["api_key"])
            args, kw = sandbox.run.call_args
            self.assertNotIn("--bare", args[1])
            self.assertNotIn(TOKEN, repr(args) + repr(broker.call_args))
            self.assertEqual(kw["input_data"], TOKEN.encode())
            self.assertNotIn(KEY, repr(kw))
            self.assertNotIn(TOKEN, repr(reply.usage) + repr(reply.transcript))

    def test_runtime_identity_and_argv_bind_mode_but_not_credentials(self):
        import sys

        from harness2.substrate.improver import ImproverSandbox

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controls = root / "controls"
            controls.mkdir()
            (controls / "mission.txt").write_text("fake mission")
            (controls / "settings.json").write_text("{}")
            ids = []
            for mode in ("subscription", "api-key"):
                sandbox = ImproverSandbox(
                    claude=Path(sys.executable), controls=controls, auth_mode=mode
                )
                with patch.dict(
                    os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": KEY}
                ):
                    ids.append(sandbox.identity())
                    args = sandbox.argv(root, ["/opt/claude/claude"], broker=root / "fake.sock")
                self.assertEqual(ids[-1]["authentication"], mode)
                self.assertEqual(args[args.index("--auth") + 1], mode)
                for secret in (TOKEN, KEY):
                    self.assertNotIn(secret, repr(args) + repr(ids[-1]))
            self.assertNotEqual(ids[0], ids[1])

    def test_entry_refuses_a_real_key_instead_of_the_placeholder(self):
        from harness2.substrate.improver_entry import client_env

        with self.assertRaises(ValueError) as err:
            client_env("api-key", io.BytesIO(KEY.encode()))
        self.assertNotIn(KEY, str(err.exception))
