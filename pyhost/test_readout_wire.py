#!/usr/bin/env python3
"""The readout opcodes (17-22) between the engine daemon and KernelClient (spec section 3). CPU only: the real
_serve_conn and the real KernelClient over a socketpair, with a fake lm; the delivery boundary (review ruling R5) over the
real KernelLM loop on Task 3's toy readout kernel.
Run: pixi run --no-install python3 pyhost/test_readout_wire.py -v"""
import contextlib
import io
import os
import socket
import struct
import sys
import threading
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_daemon  # noqa: E402
import readout  # noqa: E402
import wire  # noqa: E402
from test_kv_session_host import IDS, chain  # noqa: E402
from test_readout_host import LAY as HOST_LAY, P, ToyReadoutKernel, ro_lm  # noqa: E402
from test_spec_pull import PING, FakeLM, bare_client, serving  # noqa: E402

LAY1 = readout.Layout(1, 2)


def rec_bytes(pos0, tokens):
    n = len(tokens)
    emo = np.repeat(np.arange(pos0, pos0 + n, dtype=np.float32), 3).reshape(n, 1, 3)
    return readout.pack_records(LAY1, pos0, tokens, readout.SOURCE_GENERATED, emo, np.ones(n, np.uint8))


class FakeReadoutLM(FakeLM):
    """FakeLM plus the readout calls. Its speculative loop sends, through the sink, the record of each token before the
    next one is handed out, as readout.SpecEmitter times them. `refuse` makes a call raise ReadoutError with a code;
    FakeLM's fail_after / fail_with apply to the readout stream too."""

    def __init__(self, events, tokens, **kw):
        super().__init__(events, tokens, **kw)
        self.calls, self.refuse = [], {}

    def _call(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.refuse:
            raise readout.ReadoutError(self.refuse[name], f"{name} refused")

    def readout_config(self, *a):
        self._call("config", *a)

    def readout_off(self):
        self._call("off")

    def steer_set(self, *a):
        self._call("steer", *a)

    def steer_clear(self):
        self._call("clear")

    def readout_take(self, pos0, tokens, generated):
        self._call("take", pos0, list(tokens), generated)
        return rec_bytes(pos0, tokens)

    def spec_generate_stream(self, ids, max_new_tokens=1024, stop_ids=(), vb=8, readout_sink=None, fresh=False):
        self.requests.append((list(ids), max_new_tokens, list(stop_ids), vb, readout_sink is not None, fresh))
        g = self._ro_loop(readout_sink)
        self.gens.append(g)
        return g

    def _ro_loop(self, sink):
        y = 0
        try:
            for tok in self.tokens:
                if self.fail_after is not None and y == self.fail_after:
                    raise self.fail_with
                if y and sink is not None:
                    sink(*rec_bytes(y - 1, [self.tokens[y - 1]]))
                    self.events.append(("record", y - 1))
                y += 1
                self.events.append(("yield", y))
                try:
                    yield tok
                except GeneratorExit:
                    self.events.append(("closed", y))
                    raise
        finally:
            self.events.append(("finally", y))


class ReadoutWire(unittest.TestCase):
    def test_the_codecs_round_trip(self):
        body = wire.pack_readout_config([20, 24, 29], "a" * 64, [36, 44], "b" * 64, 8)
        self.assertEqual(wire.unpack_readout_config(body), {"emo_layers": [20, 24, 29], "directions": "a" * 64,
                                                            "ws_layers": [36, 44], "lens": "b" * 64, "k": 8})
        body = wire.pack_readout_config([20], "a" * 64, [], "", 0)
        self.assertEqual(wire.unpack_readout_config(body)["ws_layers"], [])
        body = wire.pack_steer_set(24, "a" * 64, "calm", 0.5, 41)
        self.assertEqual(wire.unpack_steer_set(body), {"layer": 24, "directions": "a" * 64, "name": "calm",
                                                       "strength": 0.5, "from_pos": 41})
        body = wire.pack_readout_take(7, [1, 2, 3], True)
        self.assertEqual(wire.unpack_readout_take(body), {"pos0": 7, "tokens": [1, 2, 3], "generated": True})
        with self.assertRaises(ValueError):
            wire.pack_steer_set(24, "a" * 65, "calm", 0.5, 1)     # an ID longer than its field

    def test_the_daemon_forwards_each_call_and_its_arguments(self):
        events = []
        lm = FakeReadoutLM(events, [100])
        with serving(lm, events) as client:
            self.assertTrue(client.readout_supported())
            client.readout_config([20, 24], "a" * 64, [44], "b" * 64, 8)
            client.steer_set(24, "a" * 64, "calm", 0.5, 41)
            client.steer_clear()
            client.readout_off()
            got = client.readout_take(5, [7, 8], True)
        self.assertEqual(lm.calls, [("off",), ("config", [20, 24], "a" * 64, [44], "b" * 64, 8),
                                    ("steer", 24, "a" * 64, "calm", 0.5, 41), ("clear",), ("off",),
                                    ("take", 5, [7, 8], True)])
        self.assertEqual(got, rec_bytes(5, [7, 8]))

    def test_a_refusal_reaches_the_serve_as_its_code_and_the_connection_stays_in_step(self):
        events = []
        lm = FakeReadoutLM(events, [100])
        lm.refuse = {"config": readout.UNKNOWN_ASSET, "take": readout.NOT_CONFIGURED, "off": readout.NO_READOUT}
        with serving(lm, events) as client:
            with self.assertRaises(readout.ReadoutError) as cm:        # the in-process backend's exception (ruling R6)
                client.readout_config([20], "a" * 64, [], "", 0)
            self.assertEqual((cm.exception.code, str(cm.exception)), (readout.UNKNOWN_ASSET, "readout_config rc=-3"))
            with self.assertRaises(readout.ReadoutError) as cm:
                client.readout_take(0, [1], False)
            self.assertEqual((cm.exception.code, str(cm.exception)), (readout.NOT_CONFIGURED, "readout_take rc=-9"))
            self.assertFalse(client.readout_supported())
            self.assertEqual(client._rc_call(PING), 0)

    def test_records_arrive_before_the_token_that_follows_them(self):
        events = []
        lm = FakeReadoutLM(events, [100, 101, 102])
        got = []

        def sink(count, raw):
            got.append(("rec", np.frombuffer(raw, LAY1.dtype)["pos"].tolist()))

        with serving(lm, events) as client:
            for tok in client.spec_generate_stream([1, 2], max_new_tokens=3, stop_ids=(), vb=8,
                                                   readout_sink=sink, fresh=True):
                got.append(("tok", tok))
            self.assertFalse(client._dirty, "a readout stream that ended cleanly keeps the connection")
            self.assertEqual(client._rc_call(PING), 0)
        self.assertEqual(got, [("tok", 100), ("rec", [0]), ("tok", 101), ("rec", [1]), ("tok", 102)])
        self.assertEqual(lm.requests, [([1, 2], 3, [], 8, True, True)])

    def test_the_plain_pull_stream_still_passes_no_readout_arguments(self):
        events = []
        lm = FakeLM(events, [100, 101])              # the old fake accepts exactly the old arguments
        with serving(lm, events) as client:
            self.assertEqual(list(client.spec_generate_stream([1], max_new_tokens=2, stop_ids=(), vb=8)), [100, 101])

    def test_a_readout_error_inside_the_stream_keeps_its_code(self):          # review ruling R6
        events = []
        lm = FakeReadoutLM(events, [100, 101], fail_after=1,
                           fail_with=readout.ReadoutError(readout.NOT_CONFIGURED, "readouts are not configured"))
        with serving(lm, events) as client:
            g = client.spec_generate_stream([1], max_new_tokens=4, stop_ids=(), vb=8, readout_sink=lambda count, raw: None)
            self.assertEqual(next(g), 100)
            with self.assertRaises(readout.ReadoutError) as cm:
                next(g)
            self.assertEqual((cm.exception.code, str(cm.exception)),
                             (readout.NOT_CONFIGURED, "engine daemon spec stream rc=-9"))
            self.assertTrue(client._dirty)
        self.assertIn(("send", "rc-9"), events)
        self.assertNotIn(("send", "rc-99"), events)
        self.assertLess(events.index(("finally", 1)), events.index(("send", "rc-9")),
                        "the loop's finally (the settle and the commit) runs before the client hears of the refusal")

    def test_a_daemon_that_predates_the_readout_opcodes_refuses_each_cleanly(self):
        d, c = socket.socketpair()
        d.settimeout(10)
        c.settimeout(10)

        def old_daemon():
            while True:
                if wire.recv_msg(d) is None:
                    break
                wire.send_msg(d, struct.pack("<i", -1))
            d.close()

        t = threading.Thread(target=old_daemon, daemon=True)
        t.start()
        client = bare_client(c)
        self.assertFalse(client.readout_supported())
        for call in (lambda: client.readout_config([20], "a" * 64, [], "", 0),
                     lambda: client.steer_set(24, "a" * 64, "calm", 0.5, 3),
                     client.steer_clear, client.readout_off,
                     lambda: client.readout_take(0, [1], False)):
            with self.assertRaisesRegex(RuntimeError, "predates this serve's protocol"):
                call()
        with self.assertRaisesRegex(RuntimeError, "spec stream rc=-1"):
            next(client.spec_generate_stream([1], max_new_tokens=2, stop_ids=(), vb=8,
                                             readout_sink=lambda count, raw: None))
        t.join(10)
        c.close()


NEXT = struct.pack("<I", 1) + struct.pack("<B", wire.OP_SPEC_NEXT)


class Client:
    """The daemon's end of a client that leaves at a chosen frame. The daemon's frames are counted from 0: frame `fail`
    raises BrokenPipeError, and so does every frame after it (a client that left stays gone). The inbox holds one
    OP_SPEC_PULL_RO request for IDS (20 tokens, vb 4) and enough next-token messages. `sent` holds what went out, decoded:
    ("records", [positions]), ("token", t), ("end",) or ("rc", code). `tried` counts every frame the daemon tried."""

    def __init__(self, fail):
        req = (struct.pack("<Bi", wire.OP_SPEC_PULL_RO, len(IDS)) + np.asarray(IDS, np.int32).tobytes()
               + struct.pack("<iii", 20, 4, 0) + struct.pack("<i", 0))
        self.inbox = bytearray(struct.pack("<I", len(req)) + req + NEXT * 64)
        self.fail, self.tried, self.sent = fail, 0, []

    def sendall(self, data):
        body = data[4:]
        (n,) = struct.unpack_from("<i", body, 0)
        self.tried += 1
        if self.tried > self.fail:
            raise BrokenPipeError("the client is gone")
        if n == 2:
            self.sent.append(("records", np.frombuffer(body[8:], HOST_LAY.dtype)["pos"].tolist()))
        elif n == 1:
            self.sent.append(("token", struct.unpack_from("<i", body, 4)[0]))
        else:
            self.sent.append(("end",) if n == 0 else ("rc", n))

    def recv(self, k):
        out = bytes(self.inbox[:k])
        del self.inbox[:k]
        return out


class DeliveryBoundary(unittest.TestCase):
    """Review ruling R5 through the daemon: the real _serve_conn and _spec_pull over the real KernelLM loop on the toy
    readout kernel (Task 3). A send that fails ends the stream at the delivery boundary: the target and the drafter keep
    rows only through the last position whose token frame and record frame both went out, the turn commits once, and
    the daemon sends nothing after the failure, no error frame either. vb 4, script [3, 3]: the seed, then bursts of four."""

    def serve(self, kernel, fail):
        client = Client(fail)
        with contextlib.redirect_stdout(io.StringIO()):
            engine_daemon._serve_conn(client, ro_lm(kernel))
        self.assertEqual(client.tried, fail + 1, "the daemon tried nothing after the frame that failed")
        self.assertEqual(kernel.calls("commit"), [("commit",)])
        return client.sent

    def test_a_record_that_cannot_be_sent_keeps_rows_only_through_the_last_delivered_position(self):
        k = ToyReadoutKernel(script=[3, 3])
        sent = self.serve(k, 2)                          # the seed's record: frame 2
        self.assertEqual(sent, [("records", list(range(P))), ("token", chain(IDS[-1], 1)[0])])
        self.assertEqual((k.kv, k.dr), (P, P), "the seed's position was not delivered: its row goes")

    def test_a_token_that_cannot_be_sent_keeps_its_predecessor_whose_record_went_out(self):
        k = ToyReadoutKernel(script=[3, 3])
        seed = chain(IDS[-1], 1)[0]
        sent = self.serve(k, 3)                          # the first draft's token, right after the seed's record
        self.assertEqual(sent, [("records", list(range(P))), ("token", seed), ("records", [P])])
        self.assertEqual((k.kv, k.dr), (P + 1, P + 1), "the seed's position was delivered: its row stays")
        self.assertEqual(k.toks, IDS + [seed])

    def test_prompt_records_that_cannot_be_sent_end_the_stream_before_its_first_token(self):
        k = ToyReadoutKernel(script=[3, 3])
        self.assertEqual(self.serve(k, 0), [])
        self.assertEqual((k.kv, k.dr), (P, P))
        self.assertEqual(k.calls("draft"), [], "no token was handed out")

    def test_the_fallback_keeps_rows_only_through_the_last_delivered_position(self):    # the drafter trails (R53)
        k = ToyReadoutKernel(cached=4, feeds=False)
        sent = self.serve(k, 2)                          # the first token's record, after the step that fed it
        self.assertEqual(sent, [("records", list(range(4, P))), ("token", chain(IDS[-1], 1)[0])])
        self.assertEqual((k.kv, k.dr), (P, 4), "the stepped row goes; the drafter stays where it trails")

    def test_the_fallback_keeps_a_delivered_predecessor_when_the_next_token_cannot_be_sent(self):    # Codex's re-review
        k = ToyReadoutKernel(cached=4, feeds=False)
        sent = self.serve(k, 3)                          # the second token's frame, right after the first token's record
        self.assertEqual(sent, [("records", list(range(4, P))), ("token", chain(IDS[-1], 1)[0]), ("records", [P])])
        self.assertEqual((k.kv, k.dr), (P + 1, 4), "position P was delivered: its row stays; the drafter still trails")


if __name__ == "__main__":
    unittest.main()
