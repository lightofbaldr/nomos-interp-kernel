import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.core.clitools import CliReply
from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec
from harness2.substrate import login_tools as client
from harness2.substrate.improver_audit import load_attempt
from harness2.substrate.login_tools_server import ALLOWED, Server
from harness2.substrate.sandbox import ProcessResult, SandboxTeardownError


def stream(*, tools=ALLOWED):
    return (
        json.dumps({"type": "system", "subtype": "init", "tools": list(tools)})
        + "\n"
        + json.dumps({"type": "result", "subtype": "success", "result": "done"})
        + "\n"
    )


def admit(controls):
    """Evidence normally emitted by run_client, for client-replacement fixtures."""
    nonce = json.loads((controls / "server.json").read_text())["nonce"]
    client.publish_admission(controls, list(ALLOWED), nonce)
    (controls / "inventory.json").write_text(
        json.dumps(
            {"observed": [list(ALLOWED)], "admitted": True, "revoked": True, "stop_reason": None}
        )
    )
    return nonce


class LoginClient(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-login-client-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        for name in ("context", "workspace"):
            (self.work / name).mkdir()
        self.probe = Mock(mojo=self.root / "fake-toolchain")
        self.identity = {"policy": client.POLICY, "probe": {"fixture": 1}}
        self.spec = ChannelSpec("claude_cli", "opus", sandbox="login-tools")
        self.patch = patch.object(
            client, "runtime", return_value=(Path("/fake/claude"), self.probe)
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.auth = patch.object(
            client, "login_status", return_value={"loggedIn": True, "authMethod": "claude.ai"}
        )
        self.auth.start()
        self.addCleanup(self.auth.stop)

    def prepare(self):
        return client.prepare(
            self.work, self.spec, "Edit context/harness/a.md", "system", self.identity
        )

    def test_flags_config_and_environment_exact_no_api_or_parent_session(self):
        controls, cmd = self.prepare()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1].split(","), list(ALLOWED))
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        for flag in ("--dangerously-skip-permissions", "--bare", "--continue", "--resume"):
            self.assertNotIn(flag, cmd)
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "fake-parent-key",
                "ANTHROPIC_AUTH_TOKEN": "fake-token",
                "ANTHROPIC_BASE_URL": "http://not-a-provider/",
                "CLAUDE_CODE_OAUTH_TOKEN": "fake-oauth",
                "CLAUDECODE": "1",
                "PYTHONPATH": "/fake/import",
            },
        ):
            env = client.environment()
        self.assertFalse(any(k.startswith("ANTHROPIC_") for k in env))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["HOME"], str(Path.home()))
        mcp = json.loads((controls / "mcp.json").read_text())
        self.assertEqual(list(mcp["mcpServers"]), ["harness2"])
        server = mcp["mcpServers"]["harness2"]
        self.assertEqual(server["command"], "/usr/bin/env")
        self.assertEqual(server["args"][0], "-i")
        self.assertIn("-I", server["args"])
        self.assertIn(str(client.ENTRY), server["args"])
        self.assertEqual(server["env"], {})
        self.assertNotIn("fake-parent", json.dumps(mcp))
        settings = json.loads((controls / "settings.json").read_text())
        self.assertTrue(settings["disableAllHooks"])
        self.assertEqual(settings["enabledPlugins"], {})
        mission = (controls / "mission.txt").read_text()
        self.assertIn("workspace/harness/a.md", mission)
        self.assertNotIn("Edit context/harness", mission)

    def test_session_nonce_atomic_admission_mode_and_no_overwrite(self):
        controls, _ = self.prepare()
        nonce = json.loads((controls / "server.json").read_text())["nonce"]
        self.assertTrue(client.valid_nonce(nonce))
        other = self.root / "next" / "work"
        for name in ("context", "workspace"):
            (other / name).mkdir(parents=True)
        second, _ = client.prepare(other, self.spec, "m", "s", self.identity)
        self.assertNotEqual(nonce, json.loads((second / "server.json").read_text())["nonce"])
        link = os.link

        def publish(source, target):
            self.assertFalse(target.exists())
            self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads(Path(source).read_text()), {"tools": list(ALLOWED), "nonce": nonce}
            )
            return link(source, target)

        with patch.object(client.os, "link", side_effect=publish):
            client.publish_admission(controls, list(ALLOWED), nonce)
        self.assertEqual((controls / "admission.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((controls / "admission.json").stat().st_nlink, 1)
        self.assertFalse(list(controls.glob(".admit-*")))
        (second / "admission.json").write_text("pre-existing")
        with self.assertRaises(FileExistsError):
            client.publish_admission(second, list(ALLOWED), "b" * 64)
        self.assertEqual((second / "admission.json").read_text(), "pre-existing")
        self.assertFalse(list(second.glob(".admit-*")))

    def test_policy_inventory_and_forbidden_calls_fail_closed(self):
        client.check_stream(stream())
        client.check_stream(stream(tools=(*ALLOWED, "EndConversation")))
        for tools in (
            (),
            (*ALLOWED, "Bash"),
            (*ALLOWED, "WebFetch"),
            (*ALLOWED, "Unknown"),
            ALLOWED[:-1],
        ):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                client.check_stream(stream(tools=tools))
        with self.assertRaises(ValueError):
            client.check_stream('{"type":"result","subtype":"success"}\n')
        with self.assertRaises(ValueError):
            client.check_event(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
                }
            )
        with self.assertRaises(client.TerminalStop):
            client.check_event(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "EndConversation"}]},
                }
            )

    def test_live_hold_before_prepare_or_process(self):
        with (
            patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": ""}),
            patch.object(client, "run_client", side_effect=AssertionError("must not launch")),
            self.assertRaisesRegex(ValueError, "not approved"),
        ):
            client.run_login_tools(
                self.work, self.spec, "m", "s", 10, expected_identity=self.identity
            )
        self.assertFalse((self.root / "login-tools").exists())

    def test_auth_metadata_refuses_api_or_token_logins_without_retaining_prose(self):
        self.auth.stop()
        for method in ("api_key", "oauth_token", "api_key_helper", "third_party", "none"):
            with (
                patch.object(
                    client.subprocess,
                    "run",
                    return_value=Mock(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "loggedIn": True,
                                "authMethod": method,
                                "email": "private@example.invalid",
                            }
                        ),
                    ),
                ),
                self.assertRaisesRegex(ValueError, "subscription login not confirmed"),
            ):
                client.login_status(Path("/fake/claude"), self.work)
        with patch.object(
            client.subprocess,
            "run",
            return_value=Mock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "email": "private@example.invalid",
                    }
                ),
            ),
        ) as run:
            self.assertEqual(
                client.login_status(Path("/fake/claude"), self.work),
                {"loggedIn": True, "authMethod": "claude.ai"},
            )
            self.assertEqual(run.call_args.args[0], ["/fake/claude", "auth", "status"])

    def test_approved_fake_client_retains_bound_tool_audit_no_spend_flag_needed(self):
        def run(*args, **kwargs):
            nonce = admit(self.root / "login-tools")
            server = Server(
                self.work / "context",
                self.work / "workspace",
                self.root / "login-tools",
                self.probe,
                nonce=nonce,
            )
            server.call("write_file", {"path": "workspace/a.md", "content": "candidate"})
            server.close()
            (self.root / "login-tools/admission.json").unlink()
            return ProcessResult(0, stream(), "", 0.1)

        with (
            patch.dict(
                os.environ,
                {"HARNESS2_MOJO_LIVE_APPROVED": "1", "HARNESS2_MOJO_IMPROVER_API_APPROVED": ""},
            ),
            patch.object(client, "runtime_identity", return_value=self.identity),
            patch.object(client, "run_client", side_effect=run),
        ):
            reply = client.run_login_tools(
                self.work, self.spec, "m", "s", 10, expected_identity=self.identity
            )
        self.assertTrue(reply.ok)
        self.assertEqual(reply.transcript["stdout"], stream())
        controls = self.root / "login-tools"
        self.assertTrue(client.verify_session(controls, accepted=True))
        (controls / "calls.jsonl").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            client.verify_session(controls)

    def test_incomplete_tool_call_is_fatal_and_session_is_retained(self):
        def run(*args, **kwargs):
            server = Server(
                self.work / "context",
                self.work / "workspace",
                self.root / "login-tools",
                self.probe,
                nonce=kwargs["nonce"],
            )
            server.append({"event": "call", "call": 1})
            server.close()
            return ProcessResult(0, stream(), "", 0.1)

        with (
            patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
            patch.object(client, "runtime_identity", return_value=self.identity),
            patch.object(client, "run_client", side_effect=run),
            self.assertRaises(SandboxTeardownError),
        ):
            client.run_login_tools(
                self.work, self.spec, "m", "s", 10, expected_identity=self.identity
            )
        self.assertTrue((self.root / "login-tools/session.json").exists())

    def test_accepted_audit_requires_complete_admission_and_success_evidence(self):
        controls, _ = self.prepare()
        client.bind_session(controls)
        self.assertTrue(client.verify_session(controls))  # failed/partial evidence retained
        with self.assertRaisesRegex(ValueError, "incomplete accepted"):
            client.verify_session(controls, accepted=True)

    def test_terminal_stop_is_recorded_even_when_tool_teardown_is_incomplete(self):
        def run(*args, **kwargs):
            controls = self.root / "login-tools"
            (controls / "calls.jsonl").write_text('{"event":"open"}\n')
            return ProcessResult(-15, stream(), "", 0.1, "end_conversation")

        with (
            patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
            patch.object(client, "runtime_identity", return_value=self.identity),
            patch.object(client, "run_client", side_effect=run),
            self.assertRaises(SandboxTeardownError),
        ):
            client.run_login_tools(
                self.work, self.spec, "m", "s", 10, expected_identity=self.identity
            )
        audit = json.loads((self.root / "login-tools/reply-audit.json").read_text())
        self.assertEqual(audit["failure"], "end_conversation")
        self.assertEqual(audit["exception"], "SandboxTeardownError")
        self.assertIsNone(audit["harness_writes"])

    def test_no_write_edit_fails_but_text_only_stage_can_return_text(self):
        for label, editing in (("edit", True), ("text", False)):
            stage = self.root / label / "work"
            for name in ("context", "workspace"):
                (stage / name).mkdir(parents=True)

            def run(*args, **kwargs):
                server = Server(
                    stage / "context",
                    stage / "workspace",
                    stage.parent / "login-tools",
                    self.probe,
                    nonce=kwargs["nonce"],
                )
                server.close()
                return ProcessResult(0, stream(), "", 0.1)

            with (
                patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
                patch.object(client, "runtime_identity", return_value=self.identity),
                patch.object(client, "run_client", side_effect=run),
            ):
                reply = client.run_login_tools(
                    stage,
                    self.spec,
                    "m",
                    "s",
                    10,
                    expected_identity=self.identity,
                    require_harness_write=editing,
                )
            self.assertEqual(reply.ok, not editing)
            audit = json.loads((stage.parent / "login-tools/reply-audit.json").read_text())
            self.assertEqual(audit["failure"], "no_harness_written" if editing else None)
            self.assertEqual(audit["text_returning"], not editing)

    def test_end_conversation_never_counts_as_success(self):
        for label, editing in (("edit", True), ("text", False)):
            stage = self.root / label / "work"
            for name in ("context", "workspace"):
                (stage / name).mkdir(parents=True)

            def run(*args, **kwargs):
                controls = stage.parent / "login-tools"
                nonce = admit(controls)
                server = Server(
                    stage / "context", stage / "workspace", controls, self.probe, nonce=nonce
                )
                server.call("write_file", {"path": "workspace/harness/a.md", "content": "written"})
                server.close()
                (controls / "admission.json").unlink()
                lines = stream(tools=(*ALLOWED, "EndConversation")).splitlines()
                lines.insert(
                    1,
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [{"type": "tool_use", "name": "EndConversation"}]
                            },
                        }
                    ),
                )
                return ProcessResult(0, "\n".join(lines) + "\n", "", 0.1)

            with (
                patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}),
                patch.object(client, "runtime_identity", return_value=self.identity),
                patch.object(client, "run_client", side_effect=run),
            ):
                reply = client.run_login_tools(
                    stage,
                    self.spec,
                    "m",
                    "s",
                    10,
                    expected_identity=self.identity,
                    require_harness_write=editing,
                )
            self.assertFalse(reply.ok)
            self.assertIn("end_conversation", reply.error)
            self.assertEqual(
                json.loads((stage.parent / "login-tools/reply-audit.json").read_text())["failure"],
                "end_conversation",
            )

    def test_real_local_stdio_entry_no_model_or_compiler_is_invoked(self):
        # Fake-format toolchain files are hashed, NEVER executed. This exercises
        # the actual absolute -I entry and MCP stdio with env -i.
        from harness2.substrate.login_tools_server import ProbeSandbox

        for rel in ("bin/mojo", "lib/mojo/std.mojoc", "share/max/modular.cfg"):
            path = self.probe.mojo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fake, never executed")
        self.identity["probe"] = ProbeSandbox(mojo=self.probe.mojo).identity()
        controls, _ = self.prepare()
        admit(controls)
        mcp = json.loads((controls / "mcp.json").read_text())["mcpServers"]["harness2"]
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "workspace/file", "content": "ok"},
                },
            },
        ]
        done = subprocess.run(
            [mcp["command"], *mcp["args"]],
            input="".join(json.dumps(x) + "\n" for x in requests),
            text=True,
            capture_output=True,
            timeout=10,
            env={"ANTHROPIC_API_KEY": "fake-inherited-key"},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.work / "workspace/file").read_text(), "ok")
        self.assertNotIn("fake-inherited-key", done.stdout + done.stderr)
        self.assertEqual(client.read_calls(controls)[-1]["event"], "close")

    def test_real_fake_host_process_capture_timeout_and_output_cap(self):
        mission = self.root / "input.txt"
        mission.write_text("fixture")
        program = "import sys; print(" + repr(stream()) + ", end='')"
        done = client.run_client(
            [sys.executable, "-I", "-c", program], self.work, mission, nonce="a" * 64, timeout=5
        )
        self.assertEqual(done.stdout, stream())
        self.assertEqual(done.returncode, 0)
        self.assertFalse((self.root / "admission.json").exists())
        self.assertTrue(json.loads((self.root / "inventory.json").read_text())["revoked"])
        for name in ("admission-record.json", "inventory.json"):
            (self.root / name).unlink()
        with patch.object(client, "MAX_OUTPUT", 100):
            done = client.run_client(
                [sys.executable, "-I", "-c", "print('x'*2000)"],
                self.work,
                mission,
                nonce="a" * 64,
                timeout=5,
            )
        self.assertEqual(done.reason, "output_limit")
        (self.root / "inventory.json").unlink()
        done = client.run_client(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"],
            self.work,
            mission,
            nonce="a" * 64,
            timeout=1,
        )
        self.assertEqual(done.reason, "timeout")

    def test_fake_host_process_widened_inventory_is_stopped(self):
        mission = self.root / "input.txt"
        mission.write_text("fixture")
        done = client.run_client(
            [sys.executable, "-I", "-c", "print(" + repr(stream(tools=("Bash",))) + ")"],
            self.work,
            mission,
            nonce="a" * 64,
            timeout=5,
        )
        self.assertEqual(done.reason, "tool_policy")

    def test_admitted_terminal_stop_revokes_file_and_retains_observed_inventory(self):
        mission = self.root / "input.txt"
        mission.write_text("fixture")
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "EndConversation"}]},
        }
        stdout = stream(tools=(*ALLOWED, "EndConversation")) + json.dumps(event) + "\n"
        done = client.run_client(
            [sys.executable, "-I", "-c", "print(" + repr(stdout) + ", end='')"],
            self.work,
            mission,
            nonce="a" * 64,
            timeout=5,
        )
        self.assertEqual(done.reason, "end_conversation")
        self.assertFalse((self.root / "admission.json").exists())
        doc = json.loads((self.root / "inventory.json").read_text())
        self.assertTrue(doc["admitted"])
        self.assertTrue(doc["revoked"])
        self.assertEqual(doc["observed"], [list(ALLOWED) + ["EndConversation"]])


class StageIntegration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-login-stage-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.context = self.root / "input"
        (self.context / "harness").mkdir(parents=True)
        (self.context / "harness/systemprompt.md").write_text("base")
        self.audit = self.root / "audit"
        self.spec = ChannelSpec("claude_cli", "opus", sandbox="login-tools")

    def test_all_stage_calls_use_new_mode_and_harvest_writable_copy(self):
        seen = []

        def run(work, spec, mission, system, wall, **kwargs):
            seen.append((work, mission))
            self.assertEqual((work / "context/harness/systemprompt.md").read_text(), "base")
            # Real preparation and server audit, fake client only.
            with patch.object(
                client,
                "runtime",
                return_value=(Path("/fake/claude"), Mock(mojo=Path("/fake/mojo"))),
            ):
                controls, _ = client.prepare(work, spec, mission, system, {"probe": {}})
            nonce = admit(controls)
            (controls / "login-status.json").write_text(
                json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
            )
            server = Server(work / "context", work / "workspace", controls, Mock(), nonce=nonce)
            server.call(
                "write_file", {"path": "workspace/harness/systemprompt.md", "content": "improved"}
            )
            server.close()
            (controls / "admission.json").unlink()
            (controls / "reply-audit.json").write_text(
                json.dumps(
                    {
                        "require_harness_write": True,
                        "text_returning": False,
                        "harness_writes": 1,
                        "failure": None,
                        "exception": None,
                    }
                )
            )
            client.bind_session(controls)
            return CliReply(text="done")

        with (
            patch.object(client, "runtime_identity", return_value={"fixture": 1}),
            patch.object(client, "run_login_tools", side_effect=run),
            patch(
                "harness2.core.proposer.subprocess.run", side_effect=AssertionError("old host path")
            ),
        ):
            prop = Proposer(self.spec, audit_dir=self.audit, attempts=1)
            for label in ("inv1", "dom1", "agg1", "experience", "reflection"):
                reply = prop.call(
                    "edit context/harness", self.context, label, harvest="context/harness"
                )
                self.assertEqual((reply.workspace / "systemprompt.md").read_text(), "improved")
                self.assertEqual(
                    load_attempt(self.audit, reply.attempt_audits[0])["outcome"], "accepted"
                )
                cached = prop.call(
                    "edit context/harness", self.context, label, harvest="context/harness"
                )
                self.assertTrue(cached.cached)
        self.assertEqual(len(seen), 5)
        self.assertEqual((self.context / "harness/systemprompt.md").read_text(), "base")

    def test_failure_has_no_trusted_host_fallback(self):
        with (
            patch.object(client, "runtime_identity", return_value={"fixture": 1}),
            patch.object(client, "run_login_tools", side_effect=ValueError("not approved")),
            patch(
                "harness2.core.proposer.subprocess.run", side_effect=AssertionError("host fallback")
            ),
        ):
            with self.assertRaises(ProposerError):
                Proposer(self.spec, audit_dir=self.audit, attempts=1).call(
                    "m", self.context, "inv1"
                )

    def test_mode_conflicts_rejected_and_host_and_api_key_unchanged(self):
        from harness2.benchmarks.mojo import MojoAdapter

        corpus = Mock(identity={"fixture": 1})
        for mode, auth, policy in (
            ("off", "off", "host"),
            ("off", "api-key", "mojo-claude"),
            ("login-tools", "off", "login-tools"),
        ):
            with patch.dict(
                os.environ,
                {
                    "HARNESS2_IMPROVER_MODE": mode,
                    "HARNESS2_MOJO_IMPROVER_AUTH": auth,
                    "HARNESS2_IMPROVER_CHANNEL": "claude_cli",
                },
            ):
                adapter = MojoAdapter(corpus=corpus)
            self.assertTrue(all(s.sandbox == policy for s in adapter.spec.channels.values()))
        with (
            patch.dict(
                os.environ,
                {"HARNESS2_IMPROVER_MODE": "login-tools", "HARNESS2_MOJO_IMPROVER_AUTH": "api-key"},
            ),
            self.assertRaises(ValueError),
        ):
            MojoAdapter(corpus=corpus)
        with self.assertRaises(ValueError):
            ChannelSpec("codex_cli", "model", sandbox="login-tools")
