"""Auth-independent audit controls; fake clients only, no host login or provider."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core.clitools import CliReply
from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec
from harness2.substrate.sandbox import SandboxTeardownError


def fake_reply(*, failed=False):
    reply = CliReply(text="" if failed else "done", error="fixture failure" if failed else "")
    # Assigned separately so the retention witnesses reach the old deletion bug.
    reply.transcript = {
        "stdout": '{"type":"assistant","tool":"Bash"}\n{"type":"result","result":"done"}\n',
        "stderr": "fixture stderr\n",
        "returncode": 1 if failed else 0,
        "reason": "fixture failure" if failed else None,
        "seconds": 0.2,
        "teardown": "confirmed",
    }
    return reply


class Retention(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="h2-retention-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.audit = self.root / "audit"
        self.context = self.root / "context"
        self.context.mkdir()
        (self.context / "source").write_text("input")
        self.spec = ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key")
        self.prop = Proposer(self.spec, audit_dir=self.audit, attempts=1)
        self.patch = patch("harness2.substrate.improver.runtime_identity", return_value={"fake": 1})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def call(self):
        return self.prop.call("fixture mission", self.context, "test", system="fixture system")

    def test_success_retains_scratch_tool_transcript_and_private_host_records(self):
        seen = []
        sentinel = self.root / "host-private"
        sentinel.write_text("outside")

        def run(work, *args, **kwargs):
            seen.append(work)
            (work / "probe").write_text("model scratch")
            (work / "outside-link").symlink_to(sentinel)
            os.mkfifo(work / "model-pipe")
            return fake_reply()

        with patch("harness2.substrate.improver.run_isolated", side_effect=run):
            reply = self.call()
        work = seen[0]
        self.assertTrue(work.exists(), "normal isolated attempts must not delete their scratch")
        self.assertEqual((work / "probe").read_text(), "model scratch")
        self.assertTrue((work / "outside-link").is_symlink())
        self.assertEqual(sentinel.read_text(), "outside")
        self.assertEqual(work.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(work.stat().st_mode & 0o777, 0o700)
        from harness2.substrate.improver_audit import load_attempt

        doc = load_attempt(self.audit, reply.attempt_audits[0])
        self.assertEqual(doc["outcome"], "accepted")
        self.assertEqual(doc["process"]["teardown"], "confirmed")
        self.assertEqual(doc["transcript"], "captured")
        self.assertIn("untrusted", doc["scratch"])
        self.assertEqual(
            (work.parent / "stdout.jsonl").read_text(), fake_reply().transcript["stdout"]
        )
        self.assertEqual((work.parent / "stderr.txt").read_text(), "fixture stderr\n")
        self.assertEqual((work.parent / "stdout.jsonl").stat().st_mode & 0o777, 0o600)
        self.assertEqual((work.parent / "mission.txt").read_text(), "fixture mission")
        self.assertEqual((work.parent / "system.txt").read_text(), "fixture system")
        from harness2.substrate.improver import ImproverSandbox

        args = ImproverSandbox(claude=Path(sys.executable).resolve()).argv(work, ["/bin/true"])
        self.assertIn(str(work), args)
        self.assertNotIn(str(work.parent), args)
        self.assertNotIn(str(self.audit), args)

    def test_failed_attempt_and_retry_both_remain_without_overwrite(self):
        seen = []

        def run(work, *args, **kwargs):
            seen.append(work)
            (work / "number").write_text(str(len(seen)))
            return fake_reply(failed=len(seen) == 1)

        self.prop.attempts = 2
        with (
            patch("harness2.substrate.improver.run_isolated", side_effect=run),
            patch("harness2.core.proposer.time.sleep"),
        ):
            reply = self.call()
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(all(path.exists() for path in seen))
        from harness2.substrate.improver_audit import load_attempt

        docs = [load_attempt(self.audit, ref) for ref in reply.attempt_audits]
        self.assertEqual([d["outcome"] for d in docs], ["failed", "accepted"])
        self.assertEqual([d["process"]["returncode"] for d in docs], [1, 0])
        self.assertEqual([(path / "number").read_text() for path in seen], ["1", "2"])

    def test_failure_resume_allocates_new_attempt_and_cache_checks_audit_bindings(self):
        with patch(
            "harness2.substrate.improver.run_isolated", return_value=fake_reply(failed=True)
        ):
            with self.assertRaises(ProposerError):
                self.call()
        before = list(self.audit.glob("_attempts/*/finished.json"))
        self.assertEqual(len(before), 1)
        raw = before[0].read_bytes()
        with patch("harness2.substrate.improver.run_isolated", return_value=fake_reply()) as run:
            reply = self.call()
            self.assertEqual(before[0].read_bytes(), raw)
            self.assertEqual(len(list(self.audit.glob("_attempts/*/finished.json"))), 2)
            cached = self.call()
            self.assertTrue(cached.cached)
            self.assertEqual(cached.attempt_audits, reply.attempt_audits)
            path = self.audit / reply.attempt_audits[0]["path"]
            (path / "stdout.jsonl").write_text("substituted")
            with self.assertRaisesRegex(ValueError, "audit.*changed"):
                self.call()
            self.assertEqual(run.call_count, 1)

    def test_unconfirmed_teardown_keeps_scratch_and_no_claimed_complete_transcript(self):
        seen = []

        def run(work, *args, **kwargs):
            seen.append(work)
            raise SandboxTeardownError("fixture teardown unconfirmed")

        self.prop.attempts = 3
        with (
            patch("harness2.substrate.improver.run_isolated", side_effect=run) as run_mock,
            patch.object(self.prop, "_harvest", side_effect=AssertionError("harvest")),
            self.assertRaises(SandboxTeardownError),
        ):
            self.call()
        self.assertEqual(run_mock.call_count, 1)
        self.assertTrue(seen[0].exists())
        doc = json.loads((seen[0].parent / "finished.json").read_text())
        self.assertEqual(doc["outcome"], "teardown_unconfirmed")
        self.assertEqual(doc["transcript"], "unavailable")
        self.assertIsNone(doc["process"])
        self.assertFalse((seen[0].parent / "stdout.jsonl").exists())

    def test_prelaunch_refusal_has_an_explicit_absent_transcript(self):
        with (
            patch("harness2.substrate.improver.run_isolated", side_effect=ValueError("held")),
            self.assertRaises(ProposerError),
        ):
            self.call()
        paths = list(self.audit.glob("_attempts/*/finished.json"))
        self.assertEqual(len(paths), 1)
        doc = json.loads(paths[0].read_text())
        self.assertEqual(doc["outcome"], "failed")
        self.assertEqual(doc["transcript"], "unavailable")
        self.assertIsNone(doc["process"])

    def test_missing_audit_root_refuses_before_process(self):
        with (
            patch("harness2.substrate.improver.run_isolated", side_effect=AssertionError("spawn")),
            self.assertRaisesRegex(ValueError, "audit_dir"),
        ):
            Proposer(self.spec, attempts=1).call("mission", self.context, "one")

    def test_linked_attempt_parent_refuses_without_touching_target(self):
        self.audit.mkdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.audit / "_attempts").symlink_to(elsewhere, target_is_directory=True)
        with (
            patch("harness2.substrate.improver.run_isolated", side_effect=AssertionError("spawn")),
            self.assertRaises((ValueError, OSError)),
        ):
            self.call()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_transcript_write_failure_stops_without_retry_or_success_cache(self):
        from harness2.substrate import improver_audit

        put = improver_audit._put

        def broken(path, raw):
            if path.name == "stdout.jsonl":
                raise OSError("fixture audit disk failure")
            return put(path, raw)

        self.prop.attempts = 3
        with (
            patch("harness2.substrate.improver.run_isolated", return_value=fake_reply()) as run,
            patch("harness2.substrate.improver_audit._put", side_effect=broken),
            self.assertRaisesRegex(OSError, "audit disk failure"),
        ):
            self.call()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(list(self.audit.glob("_attempts/*/work"))), 1)
        self.assertFalse(list(self.audit.glob("*.meta.json")))
        self.assertFalse(list(self.audit.glob("_attempts/*/finished.json")))

    def test_reader_refuses_missing_linked_or_changed_records_without_scanning_work(self):
        from harness2.substrate.improver_audit import load_attempt

        with patch("harness2.substrate.improver.run_isolated", return_value=fake_reply()):
            reply = self.call()
        ref = reply.attempt_audits[0]
        path = self.audit / ref["path"]
        os.mkfifo(path / "work/unread-pipe")
        load_attempt(self.audit, ref)
        raw = (path / "stdout.jsonl").read_bytes()
        (path / "stdout.jsonl").unlink()
        with self.assertRaises((ValueError, OSError)):
            load_attempt(self.audit, ref)
        (path / "stdout.jsonl").symlink_to(self.context / "source")
        with self.assertRaises((ValueError, OSError)):
            load_attempt(self.audit, ref)
        (path / "stdout.jsonl").unlink()
        (path / "stdout.jsonl").write_bytes(raw)
        (path / "finished.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "audit manifest changed"):
            load_attempt(self.audit, ref)


class ClientContract(unittest.TestCase):
    def test_only_a_finished_stream_result_can_be_accepted(self):
        from harness2.substrate.improver import captured_reply
        from harness2.substrate.sandbox import ProcessResult

        final = '{"type":"result","subtype":"success","result":"done"}\n'
        good = '{"type":"assistant","message":{"content":[]}}\n' + final
        self.assertTrue(captured_reply(ProcessResult(0, good, "", 0.1)).ok)
        for text in (
            "plain text",
            '{"type":"assistant","text":"partial"}\n',
            final + "malformed tail",
            '{"type":"result","result":"done"}\n',
        ):
            with self.subTest(text=text):
                reply = captured_reply(ProcessResult(0, text, "", 0.1))
                self.assertFalse(reply.ok)
                self.assertEqual(reply.transcript["stdout"], text)
        for rc, reason in ((1, None), (137, "output_limit"), (124, "timeout")):
            with self.subTest(rc=rc):
                reply = captured_reply(ProcessResult(rc, good, "partial", 0.1, reason))
                self.assertFalse(reply.ok)
                self.assertEqual(reply.transcript["reason"], reason)

    def test_api_spend_hold_refuses_with_generic_live_flag_before_auth_or_spawn(self):
        from harness2.substrate.improver import run_isolated

        with (
            patch.dict(os.environ, {"HARNESS2_MOJO_LIVE_APPROVED": "1"}, clear=True),
            patch("harness2.substrate.improver.runtime", side_effect=AssertionError("runtime")),
            patch(
                "harness2.substrate.improver.ApiCredential.from_env",
                side_effect=AssertionError("auth"),
            ),
            self.assertRaisesRegex(ValueError, "API-key improver held"),
        ):
            run_isolated(
                Path(tempfile.gettempdir()),
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
                "mission",
                "",
                10,
            )

    def test_client_requests_message_and_tool_transcript_not_only_final_reply(self):
        from harness2.substrate.improver import prepare_client

        with tempfile.TemporaryDirectory() as temp:
            args = prepare_client(
                Path(temp),
                ChannelSpec("claude_cli", "opus", sandbox="mojo-claude", auth_mode="api-key"),
                "mission",
                "",
            )
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", args)
        self.assertIn("--no-session-persistence", args)


if __name__ == "__main__":
    unittest.main()
