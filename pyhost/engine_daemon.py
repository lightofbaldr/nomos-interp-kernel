"""Persistent engine daemon — loads the Mojo logits-engine ONCE and serves prefill/step/
prefill_cont/set_len/cache_len and the KV session store (open/prepare/commit/stats) over a UNIX
socket. The ONLY process that touches the GPU.

It survives serve restarts, so the dev loop (restart the Python serve on every change) never
reloads the 16GB model and never re-allocates GPU memory → no GB10 unified-pool leak window,
and instant serve iteration. Run in tmux on the box that holds the .so:
  NOMOS_WEIGHT_NVFP4=0 NOMOS_PRECISION_BITS=4 NOMOS_KV_SWA=0 python3 pyhost/engine_daemon.py
"""
from __future__ import annotations

import os
import socket
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wire                       # noqa: E402
from host import KernelLM         # noqa: E402
import readout                    # noqa: E402


_NEXT = struct.pack("<B", wire.OP_SPEC_NEXT)
_END = object()


class ClientGone(Exception):
    """A readout frame could not be sent: the client is gone (readout spine review ruling R5). _spec_pull's sink raises it
    inside the speculative loop, before the loop hands out another token, so the loop's finally keeps rows only through
    the last delivered position. A transport cancel, never an engine error: no error frame follows it."""


def _spec_pull(conn: socket.socket, lm: KernelLM, ids, max_tokens, stop_ids, vb, with_readout=False, fresh=False) -> bool:
    """Serve one speculative stream, PULL-driven (ledger R56; wire.py has the exchange): one token per frame, and the loop
    advances only when the client asks for the next token. While the client works the loop is parked at its yield.

    Why it may not run ahead and ship bursts, as it once did: tokens generated past the last one the client took are tokens the
    client never saw. The KV session store settles each turn to the tokens its loop handed out (R55), so a loop that ran ahead
    leaves rows the client never saw; the next turn diverges inside them and forks, which rewrites the whole conversation.

    With with_readout (OP_SPEC_PULL_RO, readout spine), the loop's records go out as readout frames through its sink, each
    before the token frame that follows it. A generated position is delivered once its token frame and its record frame
    have both gone out, and the loop keeps rows only through the last delivered position (review ruling R5; the plan's
    Global Constraints map it onto the frame order). So a record that cannot be sent raises ClientGone inside the loop,
    before it hands out another token: the loop's finally (the settle and the commit) runs inside that next(), and the
    stream ends like any stream whose client left. A readout.ReadoutError raised by the loop keeps its own code (review
    ruling R6): after the loop's finally, the stream ends with that code as its error frame, the reply every readout
    opcode gives.

    Returns True when the stream ended with its end frame or a readout error frame; the loop's finally has run by then.
    Returns False when the client went away (EOF or a connection error) or sent anything but the next-token message. The loop is
    then closed right here, at the yield of the last token the client took, so its finally has run before the daemon accepts
    another connection. Any other error raised by the loop is not the client's: it propagates (to _serve_conn, which answers -99)."""

    def sink(count: int, raw: bytes) -> None:
        try:
            wire.send_msg(conn, struct.pack("<ii", 2, count) + raw)
        except OSError as e:
            raise ClientGone() from e

    extra = {"readout_sink": sink, "fresh": fresh} if with_readout else {}
    gen = lm.spec_generate_stream(ids, max_new_tokens=max_tokens, stop_ids=stop_ids, vb=vb, **extra)
    try:
        while True:
            try:
                tok = next(gen, _END)             # the loop advances here, and only here
            except ClientGone:
                return False                      # the loop already ended, and its finally ran, inside this next()
            except readout.ReadoutError as e:
                print(f"[daemon] readout refused in the stream ({e.code}): {e}", flush=True)
                try:
                    wire.send_msg(conn, struct.pack("<i", e.code))
                except OSError:
                    return False
                return True
            try:
                if tok is _END:
                    wire.send_msg(conn, struct.pack("<i", 0))
                    return True
                wire.send_msg(conn, struct.pack("<ii", 1, int(tok)))
                if wire.recv_msg(conn) != _NEXT:
                    return False
            except OSError:
                return False
    finally:
        gen.close()


def _readout_rc(call, *args) -> int:
    """A readout opcode's reply: 0, or the code of the readout.ReadoutError the call raised. Other errors reach
    _serve_conn's handler (-99)."""
    try:
        call(*args)
        return 0
    except readout.ReadoutError as e:
        print(f"[daemon] readout refused ({e.code}): {e}", flush=True)
        return e.code


def _serve_conn(conn: socket.socket, lm: KernelLM) -> None:
    while True:
        body = wire.recv_msg(conn)
        if body is None:
            return
        op = body[0]
        try:
            if op in (wire.OP_PREFILL, wire.OP_PREFILL_REUSE, wire.OP_PREFILL_CONT):
                # OP_PREFILL_REUSE is kept for old clients and is now a plain prefill: prefix reuse moved
                # into the kernel's KV session store (OP_SESSION_PREPARE).
                (n,) = struct.unpack_from("<i", body, 1)
                ids = np.frombuffer(body, dtype=np.int32, count=n, offset=5).tolist()
                lg = lm.prefill_cont(ids) if op == wire.OP_PREFILL_CONT else lm.prefill(ids)
                resp = struct.pack("<i", 0) + lg.tobytes()
            elif op == wire.OP_STEP:
                (tok,) = struct.unpack_from("<i", body, 1)
                resp = struct.pack("<i", 0) + lm.step(tok).tobytes()
            elif op == wire.OP_SET_LEN:
                (n,) = struct.unpack_from("<i", body, 1)
                lm.set_cache_len(n)
                resp = struct.pack("<i", 0)
            elif op == wire.OP_CACHE_LEN:
                resp = struct.pack("<i", lm.cache_len())
            elif op == wire.OP_PING:
                resp = struct.pack("<i", 0)
            elif op == wire.OP_MODEL_ID:
                resp = struct.pack("<i", lm.model_id())
            elif op == wire.OP_DFLASH_LOAD:
                dflash_dir = body[1:].decode()
                lm.dflash_load(dflash_dir)
                resp = struct.pack("<i", 0)
            elif op in (wire.OP_SPEC_PULL, wire.OP_SPEC_PULL_RO):
                (n,) = struct.unpack_from("<i", body, 1)
                ids_end = 5 + 4 * n
                ids = np.frombuffer(body, dtype=np.int32, count=n, offset=5).tolist()
                max_tokens, vb, ns = struct.unpack_from("<iii", body, ids_end)
                stop_ids = np.frombuffer(body, dtype=np.int32, count=ns,
                                         offset=ids_end + 12).tolist()
                ro = op == wire.OP_SPEC_PULL_RO
                fresh = ro and struct.unpack_from("<i", body, ids_end + 12 + 4 * ns)[0] != 0
                # The complete draft/verify/accept loop stays in this GPU-owning process, and the socket is crossed once per
                # TOKEN, never per FFI call. The loop advances only when the client asks for the next token (_spec_pull).
                if not _spec_pull(conn, lm, ids, max_tokens, stop_ids, vb, with_readout=ro, fresh=fresh):
                    return          # the client went away (or broke the protocol) mid-stream: this connection is finished
                continue
            elif op == wire.OP_READOUT_CONFIG:
                cfg = wire.unpack_readout_config(body)
                resp = struct.pack("<i", _readout_rc(lm.readout_config, cfg["emo_layers"], cfg["directions"],
                                                     cfg["ws_layers"], cfg["lens"], cfg["k"]))
            elif op == wire.OP_READOUT_OFF:
                resp = struct.pack("<i", _readout_rc(lm.readout_off))
            elif op == wire.OP_STEER_SET:
                st = wire.unpack_steer_set(body)
                resp = struct.pack("<i", _readout_rc(lm.steer_set, st["layer"], st["directions"], st["name"],
                                                     st["strength"], st["from_pos"]))
            elif op == wire.OP_STEER_CLEAR:
                resp = struct.pack("<i", _readout_rc(lm.steer_clear))
            elif op == wire.OP_READOUT_TAKE:
                t = wire.unpack_readout_take(body)
                try:
                    count, recs = lm.readout_take(t["pos0"], t["tokens"], t["generated"])
                    resp = struct.pack("<i", count) + recs
                except readout.ReadoutError as e:
                    print(f"[daemon] readout refused ({e.code}): {e}", flush=True)
                    resp = struct.pack("<i", e.code)
            elif op == wire.OP_SESSION_PREPARE:
                spec, n = struct.unpack_from("<ii", body, 1)
                ids = np.frombuffer(body, dtype=np.int32, count=n, offset=9).tolist()
                resp = struct.pack("<i", lm.session_prepare(ids, spec=bool(spec)))
            elif op == wire.OP_SESSION_COMMIT:
                resp = struct.pack("<i", lm.session_commit())
            elif op == wire.OP_SESSION_STATS:
                resp = struct.pack("<i", 0) + struct.pack("<10q", *lm.session_stats())
            elif op == wire.OP_SESSION_OPEN:
                status, store_dir = lm.session_open()
                resp = struct.pack("<ii", 0, status) + store_dir.encode("utf-8", "surrogateescape")
            else:
                resp = struct.pack("<i", -1)
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] op={op} EXC: {e}", flush=True)
            resp = struct.pack("<i", -99)
        wire.send_msg(conn, resp)


def main() -> None:
    sock_path = os.environ.get("NOMOS_ENGINE_SOCKET", wire.DEFAULT_SOCK)
    lm = KernelLM()  # loads the model (the one GPU allocation for the box's lifetime)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    print(f"[daemon] READY on {sock_path}  (kvstore status={lm.session_status()})", flush=True)
    while True:
        conn, _ = srv.accept()
        print("[daemon] client connected", flush=True)
        try:
            _serve_conn(conn, lm)
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] conn error: {e}", flush=True)
        finally:
            conn.close()
            print("[daemon] client disconnected", flush=True)


if __name__ == "__main__":
    main()
