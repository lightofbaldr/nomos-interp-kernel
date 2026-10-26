"""Wire protocol for the nomos engine daemon <-> serve client (local UNIX socket).

The kernel logits-engine runs in ONE persistent daemon process (holds the GPU); the serve
is a thin client that crosses this socket for prefill/step/etc. and does sampling+grammar on
its side. Restarting the serve never touches the GPU → no model reload, no GB10 leak window.

Frame: [4-byte little-endian length][body]. Bodies are op-tagged (1 byte). Logits responses
carry the compiled profile's vocab-sized float32 vector (~1MB) — negligible over a localhost
UNIX socket vs the per-token compute.
"""
from __future__ import annotations

import socket
import struct

VOCAB = 262144
DEFAULT_SOCK = "/tmp/nomos_engine.sock"

# op codes (request)
OP_PREFILL = 1       # i32 n, i32[n] ids        -> i32 rc, f32[VOCAB] logits   (full, resets cache)
OP_STEP = 2          # i32 token                -> i32 rc, f32[VOCAB] logits
OP_PREFILL_CONT = 3  # i32 n, i32[n] ids        -> i32 rc, f32[VOCAB] logits
OP_SET_LEN = 4       # i32 n                    -> i32 rc
OP_CACHE_LEN = 5     # (none)                   -> i32 cache_len
OP_PING = 6          # (none)                   -> i32 0
OP_PREFILL_REUSE = 7 # i32 n, i32[n] ids        -> i32 rc, f32[VOCAB] logits   (legacy: a plain prefill; see OP_SESSION_PREPARE)
# 8 was OP_SPEC_STREAM, the push stream R56 retired. Its number is never reused: a serve and a daemon on either side of R56 each
# answer the other's speculative request with -1 (an opcode they do not know) and the client raises, instead of misreading frames
# (ledger R42: restart them together).
OP_DFLASH_LOAD = 9   # char[] dir               -> i32 rc
OP_MODEL_ID = 10     # (none)                   -> i32 compiled model profile id
OP_SESSION_PREPARE = 11  # i32 spec, i32 n, i32[n] ids  -> i32 cached prefix length c
OP_SESSION_COMMIT = 12   # (none)                        -> i32 tokens committed (or a negative code)
OP_SESSION_STATS = 13    # (none)                        -> i32 rc, i64[10] counters
OP_SPEC_PULL = 14    # i32 n, i32[n] ids, i32 max, i32 vb, i32 ns, i32[ns] stop ids -> a PULL stream, see below
OP_SPEC_NEXT = 15    # (no body) the one message a daemon accepts inside a spec stream: the next token, please
OP_SESSION_OPEN = 16     # (none) -> i32 rc 0, i32 status, utf-8[] the store's canonical directory (empty unless status 1)
# Readout spine (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 3). Reply codes other
# than -1 and -99 are readout.py's.
OP_READOUT_CONFIG = 17   # i32 n_emo, i32[n_emo] layers, char[64] direction-set ID, i32 n_ws, i32[n_ws] layers, char[64] lens
                         # ID, i32 k                              -> i32 rc
OP_READOUT_OFF = 18      # (none)                                 -> i32 rc (0; -2 this kernel has no readouts)
OP_STEER_SET = 19        # i32 layer, char[64] direction-set ID, char[32] direction, f32 strength, i32 from_pos -> i32 rc
OP_STEER_CLEAR = 20      # (none)                                 -> i32 rc
OP_SPEC_PULL_RO = 21     # OP_SPEC_PULL's body, then i32 fresh    -> OP_SPEC_PULL's stream plus readout frames (below)
OP_READOUT_TAKE = 22     # i32 pos0, i32 n, i32 generated, i32[n] tokens -> i32 count (or a negative code), count records
ID_BYTES, NAME_BYTES = 64, 32
# OP_SPEC_PULL_RO's stream has one more frame:  <ii> (2, count), followed by count records (readout.Layout). A token's
# record comes before the next token's frame. The daemon sends record frames without waiting for OP_SPEC_NEXT. A readout
# refusal inside the stream ends it with its own code (-2..-9), after the loop's finally, as the readout opcodes answer
# it (review ruling R6); -99 stays "the loop raised".
# A daemon older than an opcode answers it with i32 -1 alone (ledger R42: restart the serve and the daemon together).

# The speculative stream (OP_SPEC_PULL) is pull-driven, like the base path (ledger R56). The daemon runs the whole draft/verify/
# accept loop in its own process, but it advances that loop only when the client asks:
#   client -> daemon   OP_SPEC_PULL request
#   daemon -> client   <ii> (1, token)         one token per frame; the loop is now parked at that token's yield
#   client -> daemon   OP_SPEC_NEXT            sent only when the client's own consumer comes back for another token
#   ...                                         (repeat)
#   daemon -> client   <i> 0                   the loop ended on its own; its finally (the KV settle and the commit) has run
#                      <i> n < 0               an error (-99: the loop raised; -1: the daemon does not know the opcode)
# If the client's next message is EOF, a connection error, or anything but OP_SPEC_NEXT, the daemon closes the loop where it is
# parked: at the yield of the last token the client took. A loop that ran ahead would hand out tokens the client never took,
# and the turn's KV (settled to what was handed out) would hold rows the client never saw.


def send_msg(sock: socket.socket, body: bytes) -> None:
    sock.sendall(struct.pack("<I", len(body)) + body)


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock: socket.socket) -> bytes | None:
    head = recv_exact(sock, 4)
    if head is None:
        return None
    (length,) = struct.unpack("<I", head)
    return recv_exact(sock, length)


def _fixed(text: str, size: int) -> bytes:
    b = text.encode("utf-8")
    if len(b) > size:
        raise ValueError(f"{text!r} does not fit in {size} bytes")
    return b.ljust(size, b"\0")


def _text(b: bytes) -> str:
    return b.rstrip(b"\0").decode("utf-8")


def pack_readout_config(emo_layers, directions, ws_layers, lens, k) -> bytes:
    e, w = [int(x) for x in emo_layers], [int(x) for x in ws_layers]
    return (struct.pack(f"<Bi{len(e)}i", OP_READOUT_CONFIG, len(e), *e) + _fixed(directions, ID_BYTES)
            + struct.pack(f"<i{len(w)}i", len(w), *w) + _fixed(lens or "", ID_BYTES) + struct.pack("<i", int(k)))


def unpack_readout_config(body: bytes) -> dict:
    (ne,) = struct.unpack_from("<i", body, 1)
    emo = list(struct.unpack_from(f"<{ne}i", body, 5))
    off = 5 + 4 * ne
    directions = _text(body[off:off + ID_BYTES])
    off += ID_BYTES
    (nw,) = struct.unpack_from("<i", body, off)
    ws = list(struct.unpack_from(f"<{nw}i", body, off + 4))
    off += 4 + 4 * nw
    lens = _text(body[off:off + ID_BYTES])
    (k,) = struct.unpack_from("<i", body, off + ID_BYTES)
    return {"emo_layers": emo, "directions": directions, "ws_layers": ws, "lens": lens, "k": k}


def pack_steer_set(layer, directions, name, strength, from_pos) -> bytes:
    return (struct.pack("<Bi", OP_STEER_SET, int(layer)) + _fixed(directions, ID_BYTES) + _fixed(name, NAME_BYTES)
            + struct.pack("<fi", float(strength), int(from_pos)))


def unpack_steer_set(body: bytes) -> dict:
    (layer,) = struct.unpack_from("<i", body, 1)
    directions = _text(body[5:5 + ID_BYTES])
    name = _text(body[5 + ID_BYTES:5 + ID_BYTES + NAME_BYTES])
    strength, from_pos = struct.unpack_from("<fi", body, 5 + ID_BYTES + NAME_BYTES)
    return {"layer": layer, "directions": directions, "name": name, "strength": strength, "from_pos": from_pos}


def pack_readout_take(pos0, tokens, generated) -> bytes:
    t = [int(x) for x in tokens]
    return struct.pack(f"<Biii{len(t)}i", OP_READOUT_TAKE, int(pos0), len(t), 1 if generated else 0, *t)


def unpack_readout_take(body: bytes) -> dict:
    pos0, n, generated = struct.unpack_from("<iii", body, 1)
    return {"pos0": pos0, "tokens": list(struct.unpack_from(f"<{n}i", body, 13)), "generated": bool(generated)}
