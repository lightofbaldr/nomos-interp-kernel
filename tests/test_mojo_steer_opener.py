"""Host-side steering and an opener for Glimmer's Mojo rollouts, for the emotion-and-persona coding run.

Steering is set by the broker, outside the sandbox: the agent still may not send a `nomos` block itself. An opener is
text placed before the task in the same user message, pinned by its sha256.
"""

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_mojo_sampling import GREEDY_IDENTITY_KEYS, FakePiSandbox
from test_sandbox_mojo import FakeModel, UnixConnection

from harness2.benchmarks import mojo_pi_runner as R
from harness2.benchmarks.mojo import MojoAdapter
from harness2.benchmarks.mojo_tasks import Corpus
from harness2.substrate.model_broker import MODEL, ModelBroker

STEER = {"layer": 24, "directions": "b3cddaec64724aac" + "0" * 48, "direction": "afraid", "strength": 0.5}
ENV_KEYS = ("HARNESS2_MOJO_SAMPLING", "HARNESS2_MOJO_STEER", "HARNESS2_MOJO_OPENER_FILE", "HARNESS2_MOJO_OPENER_SHA256")


def clean_env(**values):
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(values)
    return patch.dict(os.environ, env, clear=True)


class BrokerSteering(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-steer-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def post(self, data):
        with closing(UnixConnection(self.root / "model.sock")) as conn:
            conn.request("POST", "/v1/chat/completions", json.dumps(data), {"Content-Type": "application/json"})
            r = conn.getresponse()
            r.read()
            return r.status

    def test_broker_adds_the_hosts_steer_to_every_forwarded_request(self):
        body = {"model": MODEL, "messages": [], "max_tokens": 32, "temperature": 1.0}
        with FakeModel() as fake, ModelBroker(self.root / "model.sock", steer=STEER, _connection=fake.connection):
            self.assertEqual(self.post(body), 200)
        self.assertEqual(fake.requests, [body | {"nomos": {"steer": STEER}}])

    def test_agent_supplied_nomos_is_refused_even_when_the_host_steers(self):
        body = {"model": MODEL, "messages": [], "max_tokens": 32, "nomos": {"steer": STEER}}
        with FakeModel() as fake, ModelBroker(self.root / "model.sock", steer=STEER, _connection=fake.connection):
            self.assertEqual(self.post(body), 400)
        self.assertEqual(fake.requests, [])

    def test_without_a_steer_the_request_is_forwarded_unchanged(self):
        body = {"model": MODEL, "messages": [], "max_tokens": 32}
        with FakeModel() as fake, ModelBroker(self.root / "model.sock", _connection=fake.connection):
            self.assertEqual(self.post(body), 200)
        self.assertEqual(fake.requests, [body])

    def test_a_malformed_steer_is_refused_at_construction(self):
        for bad in (
            {k: v for k, v in STEER.items() if k != "strength"},
            STEER | {"extra": 1},
            STEER | {"strength": 2.5},
            STEER | {"strength": True},
            STEER | {"layer": "24"},
            STEER | {"direction": ""},
            STEER | {"directions": "not-hex"},
        ):
            with self.assertRaisesRegex(ValueError, "steer"):
                ModelBroker(self.root / "bad.sock", steer=bad)


class EnvParsing(unittest.TestCase):
    def test_no_steer_and_no_opener_by_default(self):
        with clean_env():
            self.assertIsNone(R.steer_from_env())
            self.assertIsNone(R.opener_from_env())

    def test_steer_is_read_as_json(self):
        with clean_env(HARNESS2_MOJO_STEER=json.dumps(STEER)):
            self.assertEqual(R.steer_from_env(), STEER)

    def test_a_malformed_steer_names_the_variable(self):
        for raw in ("afraid:0.5", json.dumps(STEER | {"strength": 9}), json.dumps([STEER])):
            with clean_env(HARNESS2_MOJO_STEER=raw):
                with self.assertRaisesRegex(ValueError, "HARNESS2_MOJO_STEER"):
                    R.steer_from_env()

    def test_opener_is_pinned_by_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "kind.txt"
            path.write_text("Hey, I'm Adam.")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            with clean_env(HARNESS2_MOJO_OPENER_FILE=str(path), HARNESS2_MOJO_OPENER_SHA256=sha):
                self.assertEqual(R.opener_from_env(), ("Hey, I'm Adam.", sha))
            with clean_env(HARNESS2_MOJO_OPENER_FILE=str(path), HARNESS2_MOJO_OPENER_SHA256="0" * 64):
                with self.assertRaisesRegex(ValueError, "HARNESS2_MOJO_OPENER_SHA256"):
                    R.opener_from_env()
            with clean_env(HARNESS2_MOJO_OPENER_FILE=str(path)):
                with self.assertRaisesRegex(ValueError, "HARNESS2_MOJO_OPENER_SHA256"):
                    R.opener_from_env()


class RecordingSandbox(FakePiSandbox):
    def __init__(self):
        self.argv = []

    def run(self, work, args, **kw):
        self.argv.append(list(args))
        return super().run(work, args, **kw)


class SteeredAndOpenedRollouts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="h2-arms-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        train, test = self.root / "train", self.root / "test"
        train.write_text(json.dumps({"id": "train", "prompt": "write a function", "kind": "run", "expect_stdout": "7"}))
        test.write_text(json.dumps({"id": "held", "prompt": "unseen", "test": 'def main():\n    print("ok")', "reference": "x"}))
        self.brokers = []
        owner = self

        class RecordingBroker:
            def __init__(self, path, **kw):
                owner.brokers.append(kw)
                self.calls = 1

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        self.sandbox = RecordingSandbox()
        self.adapter = MojoAdapter(
            corpus=Corpus(train, test), runs_root=self.root / "runs",
            runner=R.Runner(self.sandbox, approved=True, _broker=RecordingBroker), sandbox=self.sandbox,
        )
        self.opener = self.root / "kind.txt"
        self.opener.write_text("Hey, I'm Adam. Would you be okay if I called you Glimmer?")
        self.opener_sha = hashlib.sha256(self.opener.read_bytes()).hexdigest()

    def identity(self, run):
        return json.loads((run / "complete.json").read_text())["identity"]

    def test_plain_rollout_identity_and_broker_are_unchanged(self):
        with clean_env():
            ident = self.identity(self.adapter.execute("train", None, "plain"))
        self.assertEqual(set(ident), GREEDY_IDENTITY_KEYS)
        self.assertEqual(self.brokers, [{"max_calls": 32}])

    def test_steered_rollout_records_the_steer_and_hands_it_to_the_broker(self):
        with clean_env(HARNESS2_MOJO_SAMPLING="vendor", HARNESS2_MOJO_STEER=json.dumps(STEER)):
            ident = self.identity(self.adapter.execute("train", None, "steered"))
        self.assertEqual(ident["steer"], STEER)
        self.assertEqual(self.brokers, [{"max_calls": 32, "steer": STEER}])

    def test_opener_comes_before_the_task_in_one_message_and_is_recorded(self):
        with clean_env(HARNESS2_MOJO_OPENER_FILE=str(self.opener), HARNESS2_MOJO_OPENER_SHA256=self.opener_sha):
            ident = self.identity(self.adapter.execute("train", None, "opened"))
        self.assertEqual(ident["opener_sha256"], self.opener_sha)
        prompt = self.sandbox.argv[0][-1]
        self.assertTrue(prompt.startswith(self.opener.read_text() + "\n\nwrite a function\n"))
        self.assertEqual(self.brokers, [{"max_calls": 32}])

    def test_an_unsteered_rollout_never_resumes_as_steered(self):
        with clean_env():
            self.adapter.execute("train", None, "same")
        with clean_env(HARNESS2_MOJO_STEER=json.dumps(STEER)):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.adapter.execute("train", None, "same")


if __name__ == "__main__":
    unittest.main()
