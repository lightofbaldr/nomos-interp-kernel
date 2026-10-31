"""Host-side boundary/collection/wiring tests. No real credentials or providers."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-improver-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.context = self.root / "context"
        (self.context / "harness").mkdir(parents=True)
        (self.context / "harness/systemprompt.md").write_text("safe input")
        self.sentinel = self.root / "host-private"
        self.sentinel.write_text("FAKE-PRIVATE-SENTINEL")


class Collection(Fixture):
    def test_harvest_never_follows_a_model_written_symlink(self):
        (self.work / "context/harness").mkdir(parents=True)
        (self.work / "context/harness/stolen.md").symlink_to(self.sentinel)
        prop = Proposer(ChannelSpec("claude_cli", "opus"), audit_dir=self.root / "audit")
        with self.assertRaises((ValueError, OSError)):
            prop._harvest(self.work, "context/harness", "attempt")
        self.assertFalse((self.root / "audit/attempt.workspace/stolen.md").exists())

    def test_safe_tree_rejects_links_special_files_hardlinks_and_limits(self):
        from harness2.substrate.improver_files import read_tree

        p = self.work / "bad"
        p.symlink_to(self.sentinel)
        with self.assertRaises((ValueError, OSError)):
            read_tree(self.work)
        p.unlink()
        os.link(self.sentinel, p)
        with self.assertRaises(ValueError):
            read_tree(self.work)
        p.unlink()
        os.mkfifo(p)
        with self.assertRaises(ValueError):
            read_tree(self.work)
        p.unlink()
        p.write_text("x" * 10)
        with self.assertRaises(ValueError):
            read_tree(self.work, max_bytes=9)
        with self.assertRaises(ValueError):
            read_tree(self.work, max_files=0)

    def test_normal_collection_is_complete_and_context_is_not_modified(self):
        from harness2.substrate.improver_files import copy_tree, read_tree

        before = read_tree(self.context)
        copy_tree(self.context, self.work / "context")
        prop = Proposer(ChannelSpec("claude_cli", "opus"), audit_dir=self.root / "audit")
        dest = prop._harvest(self.work, "context/harness", "attempt")
        self.assertEqual(read_tree(dest), {"systemprompt.md": b"safe input"})
        self.assertEqual(read_tree(self.context), before)

    def test_single_file_reader_refuses_links_and_special_files(self):
        from harness2.substrate.improver_files import read_file

        path = self.work / "artifact"
        path.symlink_to(self.sentinel)
        with self.assertRaises(ValueError):
            read_file(path)
        path.unlink()
        os.link(self.sentinel, path)
        with self.assertRaises(ValueError):
            read_file(path)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            read_file(path)
        path.unlink()
        path.write_bytes(b"valid")
        self.assertEqual(read_file(path), b"valid")
        with self.assertRaises(ValueError):
            read_file(path, max_bytes=2)

    def test_cached_tree_or_reply_substitution_refuses_before_another_process(self):
        from harness2.core.clitools import CliReply

        spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
        prop = Proposer(spec, audit_dir=self.root / "audit")
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="isolated")
            ) as run,
        ):
            r = prop.call("mission", self.context, "cache", harvest="context/harness")
            self.assertTrue(
                prop.call("mission", self.context, "cache", harvest="context/harness").cached
            )
            file = r.workspace / "systemprompt.md"
            file.write_text("changed")
            with self.assertRaisesRegex(ValueError, "cache changed"):
                prop.call("mission", self.context, "cache", harvest="context/harness")
            file.unlink()
            file.symlink_to(self.sentinel)
            with self.assertRaises((ValueError, OSError)):
                prop.call("mission", self.context, "cache", harvest="context/harness")
            file.unlink()
            file.write_text("safe input")
            r.audit.write_text("different reply")
            with self.assertRaisesRegex(ValueError, "cache changed"):
                prop.call("mission", self.context, "cache", harvest="context/harness")
            self.assertEqual(run.call_count, 1)

    def test_harvest_subpath_and_root_must_be_confined(self):
        prop = Proposer(ChannelSpec("claude_cli", "opus"), audit_dir=self.root / "audit")
        for name in ("../context/harness", str(self.context / "harness")):
            with self.assertRaises(ValueError):
                prop._harvest(self.work, name, "attempt")
        (self.work / "context").symlink_to(self.context, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            prop._harvest(self.work, "context/harness", "attempt")

    def test_missing_or_dangling_cached_files_refuse_before_work(self):
        from harness2.core.clitools import CliReply

        prop = Proposer(
            ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
            audit_dir=self.root / "audit",
        )
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="ok")
            ) as run,
        ):
            r = prop.call("mission", self.context, "cache", harvest="context/harness")
            r.audit.unlink()
            with self.assertRaises((ValueError, OSError)):
                prop.call("mission", self.context, "cache", harvest="context/harness")
            r.audit.symlink_to(self.root / "missing")
            with self.assertRaisesRegex(ValueError, "linked"):
                prop.call("mission", self.context, "cache", harvest="context/harness")
            self.assertEqual(run.call_count, 1)
            self.assertFalse((self.root / "missing").exists())


class Boundary(Fixture):
    def sandbox(self):
        from harness2.substrate.improver import ImproverSandbox

        controls = self.root / "controls"
        controls.mkdir()
        (controls / "mission.txt").write_text("fixture")
        (controls / "settings.json").write_text("{}")
        return ImproverSandbox(claude=Path(sys.executable).resolve(), controls=controls)

    def test_full_process_namespace_has_no_host_auth_or_general_network(self):
        from harness2.substrate.improver_broker import PLACEHOLDER

        sandbox = self.sandbox()
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "FAKE-PARENT-KEY", "HTTP_PROXY": "http://outside"}
        ):
            argv = sandbox.argv(
                self.work, ["/opt/claude/claude", "-p"], broker=self.root / "model.sock"
            )
        for token in (
            "--unshare-all",
            "--disable-userns",
            "--cap-drop",
            "--clearenv",
            "--new-session",
        ):
            self.assertIn(token, argv)
        self.assertNotIn(str(Path.home()), argv)
        self.assertNotIn("FAKE-PARENT-KEY", argv)
        self.assertNotIn("/run/user/1000", argv)
        self.assertNotIn("--share-net", argv)
        self.assertNotIn(PLACEHOLDER, argv)  # client auth arrives through a pipe
        self.assertIn("http://127.0.0.1:8092", argv)
        self.assertIn("/runtime/improver-entry.py", argv)
        self.assertEqual(argv[-2:], ["/opt/claude/claude", "-p"])
        self.assertEqual(argv[argv.index("--bind") + 1], str(self.work))

    def test_process_manifest_binds_runtime_and_does_not_contain_login(self):
        sandbox = self.sandbox()
        identity = sandbox.identity()
        self.assertEqual(identity["policy"], "mojo-claude-process-v2")
        self.assertIn("claude", identity["files"])
        self.assertIn("improver", identity["files"])
        self.assertNotIn("accessToken", json.dumps(identity))
        self.assertNotIn("credential", identity["files"])

    def test_toolchain_mounts_remain_read_only_and_audit_siblings_stay_outside(self):
        from harness2.substrate.improver import ImproverSandbox

        mojo = self.root / "fake-mojo"
        for rel in ("bin", "lib", "include", "share/max"):
            (mojo / rel).mkdir(parents=True)
        (mojo / "share/max/modular.cfg").write_text(f"prefix = {mojo}\n")
        pi = self.root / "fake-pi"
        pi.mkdir()
        binary = Path(sys.executable).resolve()
        sandbox = ImproverSandbox(claude=binary, mojo=mojo, pi=pi, node=binary)
        args = sandbox.argv(self.work, ["/bin/true"])
        ro = [args[i + 1 : i + 3] for i, arg in enumerate(args) if arg == "--ro-bind"]
        for source, dest in (
            (mojo / "bin", "/opt/mojo/bin"),
            (mojo / "lib", "/opt/mojo/lib"),
            (mojo / "include", "/opt/mojo/include"),
            (pi, "/opt/pi"),
            (binary, "/opt/node/node"),
            (binary, "/opt/claude/claude"),
        ):
            self.assertIn([str(source), dest], ro)
        self.assertNotIn(str(self.root), args)
        self.assertNotIn(str(Path.home()), args)
        self.assertNotIn(str(mojo / "share"), args)
        self.assertEqual((self.work / ".modular/modular.cfg").read_text(), "prefix = /opt/mojo\n")

    def test_alias_is_not_misconfigured_as_a_literal_upstream_model(self):
        from harness2.substrate.improver import prepare_client

        controls = self.root / "inputs"
        controls.mkdir()
        prepare_client(
            controls,
            ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
            "mission",
            "",
        )
        settings = json.loads((controls / "settings.json").read_text())
        self.assertNotIn("ANTHROPIC_DEFAULT_OPUS_MODEL", settings["env"])
        prepare_client(
            controls,
            ChannelSpec(
                "claude_cli", "claude-opus-4-6", sandbox="mojo-claude", auth_mode="api-key"
            ),
            "mission",
            "",
        )
        settings = json.loads((controls / "settings.json").read_text())
        self.assertEqual(settings["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"], "claude-opus-4-6")

    def test_no_authorization_means_no_auth_read_or_process(self):
        from harness2.substrate.improver import run_isolated

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "harness2.substrate.improver.Credentials.from_env",
                side_effect=AssertionError("auth read"),
            ),
            patch("harness2.substrate.improver.Sandbox.run", side_effect=AssertionError("spawn")),
            self.assertRaisesRegex(ValueError, "approval"),
        ):
            run_isolated(self.work, ChannelSpec("claude_cli", "opus"), "mission", "", 60)

    def test_unconfirmed_teardown_refuses_not_merely_foreground_exit(self):
        from harness2.substrate.sandbox import SandboxTeardownError

        sandbox = self.sandbox()
        with patch("harness2.substrate.improver.subprocess.run") as run:
            run.return_value = Mock(
                returncode=0, stdout="LoadState=loaded\nActiveState=active\nControlGroup=/x\n"
            )
            with self.assertRaises(SandboxTeardownError):
                sandbox._stop_unit("fixture")
            run.return_value = Mock(
                returncode=4, stdout="LoadState=not-found\nActiveState=inactive\nControlGroup=\n"
            )
            sandbox._stop_unit("fixture")
            run.return_value = Mock(returncode=1, stdout="")
            with self.assertRaises(SandboxTeardownError):
                sandbox._stop_unit("fixture")

    def test_whole_launch_uses_fake_operator_key_without_copying_it(self):
        from harness2.core.clitools import CliReply
        from harness2.substrate.improver import run_isolated
        from harness2.substrate.improver_auth import PLACEHOLDER
        from harness2.substrate.sandbox import ProcessResult

        token = "FAKE-OPERATOR-KEY-NOT-IN-CLIENT"
        sandbox = Mock()
        sandbox.identity.return_value = {"fixture": "runtime"}
        seen = []

        def run(work, command, **kw):
            controls = kw["broker"].parent
            seen.append(command)
            self.assertEqual(work, self.work)
            self.assertEqual((controls / "mission.txt").read_text(), "mission")
            self.assertNotIn(token, str(command) + (controls / "settings.json").read_text())
            self.assertEqual(kw["input_data"], PLACEHOLDER.encode())
            self.assertEqual(command[0], "/opt/claude/claude")
            for arg in (
                "--bare",
                "--settings",
                "--no-session-persistence",
            ):
                self.assertIn(arg, command)
            return ProcessResult(
                0, '{"type":"result","result":"sandboxed","subtype":"success"}', "", 1
            )

        sandbox.run.side_effect = run
        spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
        with (
            patch.dict(
                os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1", "ANTHROPIC_API_KEY": token}
            ),
            patch("harness2.substrate.improver.config.CLAUDE_USE_VERTEX", False),
            patch("harness2.substrate.improver.config.CLAUDE_ISOLATION", "flags"),
            patch("harness2.substrate.improver.runtime", return_value=sandbox),
        ):
            reply = run_isolated(self.work, spec, "mission", "", 60)
            self.assertIsInstance(reply, CliReply)
            self.assertTrue(reply.ok)
            self.assertEqual(reply.usage["sandbox"]["model_calls"], 0)
            sandbox.run.side_effect = None
            sandbox.run.return_value = ProcessResult(
                137, '{"result":"untrusted success"}', "", 1, "memory"
            )
            self.assertFalse(run_isolated(self.work, spec, "mission", "", 60).ok)
            sandbox.run.return_value = ProcessResult(
                1,
                '{"type":"result","subtype":"success","result":"untrusted success",'
                '"total_cost_usd":0.7,"usage":{"output_tokens":2}}',
                "",
                1,
            )
            failed = run_isolated(self.work, spec, "mission", "", 60)
            self.assertFalse(failed.ok)
            self.assertEqual(failed.cost_usd, 0.7)
            self.assertEqual(failed.usage["output_tokens"], 2)
            sandbox.run.return_value = ProcessResult(0, '{"result":"' + token + '"}', "", 1)
            rejected = run_isolated(self.work, spec, "mission", "", 60)
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.transcript["reason"], "credential_echo")
            self.assertNotIn(token, json.dumps(rejected.transcript))
        self.assertEqual(len(seen), 1)


class Integration(Fixture):
    def spec(self):
        return ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")

    def test_sandbox_success_is_parsed_and_never_calls_host_subprocess(self):
        from harness2.core.clitools import CliReply

        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="isolated")
            ) as run,
            patch(
                "harness2.core.proposer.subprocess.run",
                side_effect=AssertionError("host execution"),
            ),
        ):
            reply = Proposer(self.spec(), attempts=1, audit_dir=self.root / "audit").call(
                "mission", self.context, "one"
            )
        self.assertEqual(reply.text, "isolated")
        self.assertEqual(run.call_count, 1)

    def test_sandbox_failure_never_falls_back_to_host(self):
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated",
                side_effect=ValueError("sandbox unavailable"),
            ),
            patch(
                "harness2.core.proposer.subprocess.run",
                side_effect=AssertionError("host execution"),
            ),
            self.assertRaisesRegex(ProposerError, "sandbox unavailable"),
        ):
            Proposer(self.spec(), attempts=1, audit_dir=self.root / "audit").call(
                "mission", self.context, "one"
            )

    def test_a_bound_failed_attempt_is_not_mistaken_for_a_success_cache(self):
        from harness2.core.clitools import CliReply

        prop = Proposer(self.spec(), attempts=1, audit_dir=self.root / "audit")
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated",
                return_value=CliReply(error="fixture failure"),
            ) as run,
        ):
            with self.assertRaises(ProposerError):
                prop.call("mission", self.context, "one")
            run.return_value = CliReply(text="isolated")
            self.assertFalse(prop.call("mission", self.context, "one").cached)
            self.assertTrue(prop.call("mission", self.context, "one").cached)
            self.assertEqual(run.call_count, 2)

    def test_cache_is_separate_from_host_and_bound_to_sandbox_runtime(self):
        from harness2.core.clitools import CliReply

        audit = self.root / "audit"
        with patch(
            "harness2.core.proposer.subprocess.run",
            return_value=Mock(
                stdout='{"result":"old host","subtype":"success"}', returncode=0, stderr=""
            ),
        ):
            Proposer(ChannelSpec("claude_cli", "opus"), audit_dir=audit).call(
                "mission", self.context, "one"
            )
        with (
            patch(
                "harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}
            ) as identity,
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="isolated")
            ) as run,
        ):
            prop = Proposer(self.spec(), audit_dir=audit)
            self.assertEqual(prop.call("mission", self.context, "one").text, "isolated")
            self.assertTrue(prop.call("mission", self.context, "one").cached)
            identity.return_value = {"fixture": 2}
            self.assertFalse(prop.call("mission", self.context, "one").cached)
            self.assertEqual(run.call_count, 2)
        metas = [json.loads(p.read_text()) for p in audit.glob("*.meta.json")]
        self.assertEqual(sum(m.get("sandbox") is not None for m in metas), 2)

    def test_linked_context_refuses_before_auth_or_execution(self):
        (self.context / "leak").symlink_to(self.sentinel)
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch("harness2.substrate.improver.run_isolated", side_effect=AssertionError("spawn")),
            self.assertRaises((ValueError, OSError)),
        ):
            Proposer(self.spec(), audit_dir=self.root / "audit").call(
                "mission", self.context, "one"
            )

    def test_cache_also_binds_effective_wall_limit_and_collection_path(self):
        from harness2.core.clitools import CliReply

        prop = Proposer(self.spec(), audit_dir=self.root / "audit")
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="isolated")
            ) as run,
        ):
            prop.call("mission", self.context, "same", timeout=10, harvest="context/harness")
            self.assertTrue(
                prop.call(
                    "mission", self.context, "same", timeout=10, harvest="context/harness"
                ).cached
            )
            self.assertFalse(
                prop.call(
                    "mission", self.context, "same", timeout=20, harvest="context/harness"
                ).cached
            )
            self.assertFalse(
                prop.call("mission", self.context, "same", timeout=20, harvest="context").cached
            )
            self.assertEqual(run.call_count, 3)

    def test_runtime_drift_stops_without_an_extra_attempt(self):
        from harness2.core.clitools import CliReply

        with (
            patch(
                "harness2.substrate.improver.runtime_identity",
                side_effect=[{"fixture": 1}, {"fixture": 2}],
            ),
            patch(
                "harness2.substrate.improver.run_isolated", return_value=CliReply(text="unusable")
            ) as run,
            self.assertRaisesRegex(ProposerError, "runtime changed"),
        ):
            Proposer(self.spec(), attempts=3, audit_dir=self.root / "audit").call(
                "mission", self.context, "one"
            )
        self.assertEqual(run.call_count, 1)

    def test_teardown_failure_is_fatal_and_retains_only_its_scratch(self):
        from harness2.substrate.sandbox import SandboxTeardownError

        audit = self.root / "audit"
        with (
            patch("harness2.substrate.improver.runtime_identity", return_value={"fixture": 1}),
            patch(
                "harness2.substrate.improver.run_isolated",
                side_effect=SandboxTeardownError("unconfirmed"),
            ) as run,
            self.assertRaisesRegex(SandboxTeardownError, "workspace retained"),
        ):
            Proposer(self.spec(), attempts=3, audit_dir=audit).call("mission", self.context, "one")
        self.assertEqual(run.call_count, 1)
        scratch = run.call_args.args[0]
        self.assertEqual((scratch / "context/harness/systemprompt.md").read_text(), "safe input")

    def test_mojo_opt_in_and_selector_inherit_isolation(self):
        from test_mojo_followups import Fixture as MojoFixture

        from harness2.core.schedule import _selector_for

        fixture = MojoFixture()
        with (
            patch("harness2.benchmarks.mojo.config.IMPROVER_MODEL", "opus"),
            patch.dict(os.environ, {"HARNESS2_MOJO_IMPROVER_AUTH": "api-key"}),
        ):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        adapter = fixture.adapter
        self.assertEqual(adapter.spec.channels["proposer"].sandbox, "mojo-claude")
        self.assertEqual(adapter.spec.channels["selector"].sandbox, "mojo-claude")
        selector = _selector_for(adapter, Proposer(ChannelSpec("claude_cli", "opus")), None)
        self.assertEqual(selector.spec.sandbox, "mojo-claude")
        with self.assertRaises(ValueError):
            ChannelSpec("codex_cli", "codex", sandbox="mojo-claude", auth_mode="api-key")
