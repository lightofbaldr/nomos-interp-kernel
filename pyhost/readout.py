"""Readout spine, host side (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, sections 1 and
3): the asset registry, the binary record the daemon and the serve share, the speculative turn's record timing, and the
serve's assembly of nomos.readout/1 records."""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np

V = "nomos.readout/1"
MAX_STRENGTH = 2.0

# Reply codes for the readout calls (KernelLM) and their opcodes (17-22). -1 stays "an opcode this daemon does not know"
# (wire.py), -99 "the engine raised".
NO_READOUT = -2         # this kernel has no readout exports
UNKNOWN_ASSET = -3      # an ID the registry does not list, or lists as the other kind
ALTERED = -4            # a file's sha256 no longer matches what the asset recorded
NO_LAYER = -5           # a requested layer the asset does not have
REFUSED = -6            # the kernel refused, or an asset does not fit the model (the message says which)
NO_DIRECTION = -7       # a direction name the set does not have
BAD_STEER = -8          # a non-finite strength or one outside [-MAX_STRENGTH, MAX_STRENGTH], or a negative start position
NOT_CONFIGURED = -9     # a read with readouts off


class ReadoutError(RuntimeError):
    """A readout request the host refuses; `code` is its reply code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def registry_path() -> str:
    return os.path.expanduser(os.environ.get("NOMOS_READOUT_REGISTRY", "~/nomos_data/readout-assets/registry.json"))


def sha256_file(path: str) -> str:
    """A file's sha256, computed every time; nothing is cached. A cache keyed by mtime could miss a rewrite within one
    timestamp tick. The daemon only loads assets when the configuration changes, which costs about a second for four
    lens layers."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


class AssetRegistry:
    """registry.json (spec section 3). An asset's ID is the sha256 of its manifest.json, which records each file's
    sha256, so changing any file changes the ID. Every load re-checks the manifest and the files it reads."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or registry_path()
        with open(self.path) as f:
            self.assets = {a["id"]: a for a in json.load(f)["assets"]}

    def get(self, asset_id: str, kind: str) -> dict:
        a = self.assets.get(asset_id)
        if a is None or a["kind"] != kind:
            raise ReadoutError(UNKNOWN_ASSET, f"no {kind} asset {asset_id!r} in {self.path}")
        return a

    def _manifest(self, a: dict) -> dict:
        path = os.path.join(a["dir"], "manifest.json")
        if sha256_file(path) != a["id"]:
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: its manifest changed on disk")
        with open(path) as f:
            return json.load(f)

    def _layer(self, a: dict, man: dict, layer: int) -> dict:
        entry = man["layers"].get(str(int(layer)))
        if entry is None:
            raise ReadoutError(NO_LAYER, f"{a['kind']} asset {a['id'][:16]} has no layer {layer}")
        return entry

    def _array(self, a: dict, entry: dict, shape: tuple) -> np.ndarray:
        path = os.path.join(a["dir"], entry["file"])
        if sha256_file(path) != entry["sha256"]:
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: {entry['file']} changed on disk")
        arr = np.fromfile(path, dtype=np.float32)
        if arr.size != int(np.prod(shape)):
            raise ReadoutError(ALTERED, f"{a['kind']} asset {a['id'][:16]}: {entry['file']} is not {shape}")
        return arr.reshape(shape)

    def directions(self, asset_id: str, layers) -> np.ndarray:
        """Unit directions [len(layers), E, D], float32."""
        a = self.get(asset_id, "directions")
        man = self._manifest(a)
        return np.ascontiguousarray(np.stack([self._array(a, self._layer(a, man, layer), (man["E"], man["D"]))
                                              for layer in layers]))

    def lens(self, asset_id: str, layers) -> tuple[np.ndarray, np.ndarray]:
        """The J-lens matrices [len(layers), D, D] (lens(h) = h @ J^T) and the final-norm weights [D], float32."""
        a = self.get(asset_id, "lens")
        man = self._manifest(a)
        mats = np.stack([self._array(a, self._layer(a, man, layer), (man["D"], man["D"])) for layer in layers])
        return np.ascontiguousarray(mats), np.ascontiguousarray(self._array(a, man["norm"], (man["D"],)))

    def steer_vector(self, asset_id: str, layer: int, name: str, strength: float) -> np.ndarray:
        """s x layer_sizes[layer] x the set's unit direction: sp1/steer.py's units. s is signed: a negative s steers away,
        like its minus- kinds (spec decision 5). Float32 [D]."""
        a = self.get(asset_id, "directions")
        if name not in a["names"]:
            raise ReadoutError(NO_DIRECTION, f"direction set {asset_id[:16]} has no direction {name!r}")
        s = float(strength)
        if not (math.isfinite(s) and abs(s) <= MAX_STRENGTH):
            raise ReadoutError(BAD_STEER, f"steering strength {strength!r} is outside [-{MAX_STRENGTH}, {MAX_STRENGTH}]")
        v = self.directions(asset_id, [layer])[0][a["names"].index(name)].astype(np.float64)
        return np.ascontiguousarray(s * float(a["layer_sizes"][int(layer)]) * v, dtype=np.float32)


SOURCE_PROMPT, SOURCE_GENERATED = 0, 1
FLAG_EMO, FLAG_WS = 1, 2


class Layout:
    """A configuration's record (spec section 3), packed little-endian: i32 pos, i32 token, u8 flags (bit0 emotion valid,
    bit1 workspace valid), u8 source, u16 reserved, f32 emo[n_emo][E+1] (E projections, then the norm), and with a
    workspace i32 ws_ids[n_ws][k] and f32 ws_scores[n_ws][k]."""

    def __init__(self, n_emo: int, E: int, n_ws: int = 0, k: int = 0) -> None:
        self.n_emo, self.E, self.n_ws, self.k = int(n_emo), int(E), int(n_ws), int(k) if n_ws else 0
        fields = [("pos", "<i4"), ("token", "<i4"), ("flags", "u1"), ("source", "u1"), ("reserved", "<u2"),
                  ("emo", "<f4", (self.n_emo, self.E + 1))]
        if self.n_ws:
            fields += [("ws_ids", "<i4", (self.n_ws, self.k)), ("ws_scores", "<f4", (self.n_ws, self.k))]
        self.dtype = np.dtype(fields)

    def __eq__(self, other) -> bool:
        return isinstance(other, Layout) and (self.n_emo, self.E, self.n_ws, self.k) == (
            other.n_emo, other.E, other.n_ws, other.k)

    def __repr__(self) -> str:
        return f"Layout(n_emo={self.n_emo}, E={self.E}, n_ws={self.n_ws}, k={self.k})"


def pack_records(layout: Layout, pos0: int, tokens, source: int, emo, emo_valid, ws_ids=None, ws_scores=None,
                 ws_valid=None) -> tuple[int, bytes]:
    """The positions [pos0, pos0 + len(tokens)) whose emotion or workspace entry is valid, as (count, records) in position
    order; tokens[i] sits at pos0 + i."""
    flags = np.asarray(emo_valid, dtype=np.uint8) & FLAG_EMO
    if layout.n_ws:
        flags = flags | ((np.asarray(ws_valid, dtype=np.uint8) & 1) << 1)
    keep = np.nonzero(flags)[0]
    rec = np.zeros(len(keep), dtype=layout.dtype)
    rec["pos"] = int(pos0) + keep
    rec["token"] = np.asarray(tokens, dtype=np.int32)[keep]
    rec["flags"] = flags[keep]
    rec["source"] = source
    rec["emo"] = np.asarray(emo, dtype=np.float32)[keep]
    if layout.n_ws:
        rec["ws_ids"] = np.asarray(ws_ids, dtype=np.int32)[keep]
        rec["ws_scores"] = np.asarray(ws_scores, dtype=np.float32)[keep]
    return int(len(keep)), rec.tobytes()


class SpecEmitter:
    """The speculative turn's record timing (spec section 3; ruling P2-R4). Once the prompt is in, its records go out.
    The record of the j-th token handed out goes out just before the (j+1)-th is handed out, so the last token handed
    out never gets one. That is the row the turn's settle cuts (ledger R55): a stop token, or the token a closed stream
    ended on. Ledger R54's exact step drops its row before its successor is handed out, so that record reads invalid and
    is not sent."""

    def __init__(self, lm, prompt_ids, sink) -> None:
        self.lm, self.sink = lm, sink
        self.prompt_ids = [int(t) for t in prompt_ids]
        self.P = len(self.prompt_ids)
        self.prev = None          # (position, token) of the last token handed out

    def start(self, P: int) -> None:
        """The prompt is in and the KV holds P rows. A KV that holds fewer rows than the prompt has tokens (a
        continuation that began with BOS lost it) cannot say which token sits where, so it sends no prompt records."""
        self.P = int(P)
        if self.P == len(self.prompt_ids):
            self._send(*self.lm.readout_records(0, self.prompt_ids, SOURCE_PROMPT))
        else:
            print(f"[readout] the KV holds {P} rows for a {len(self.prompt_ids)}-token prompt: no prompt records",
                  flush=True)

    def handed(self, tok: int) -> None:
        """Call right before handing out tok."""
        if self.prev is None:
            pos = self.P
        else:
            self._send(*self.lm.readout_records(self.prev[0], [self.prev[1]], SOURCE_GENERATED))
            pos = self.prev[0] + 1
        self.prev = (pos, int(tok))

    def _send(self, count: int, raw: bytes) -> None:
        if count:
            self.sink(count, raw)


def _g6(x) -> float:
    """A float with 6 significant digits (spec section 1). A non-finite value is a defect and is never sent."""
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"non-finite readout value {v}")
    return float(f"{v:.6g}")


class ReadoutTurn:
    """One turn's readouts as the serve sends them (spec section 1): the header once, records in position order, then a
    summary.
    - Records arrive through add(count, raw): from the speculative stream's sink, or on the base path from this
      object's own takes between steps (take(pos0, tokens, generated) -> (count, raw)).
    - note_token() runs for every token handed out, in order, with its channel. It always runs before that token's
      record can arrive.
    - The turn's origin P, the KV's length once the prompt is in, comes from the engine, never from len(prompt_ids)
      (review ruling R9). A continuation that began with BOS loses it in the KV, which then holds one row fewer than the
      prompt has tokens (engine_decode.mojo:153-160); its prompt has no records (SpecEmitter.start sends none, and the
      base path takes none). The base path asks kv_len() right after the prefill, before its first take. The speculative
      path takes P from its first generated record, which sits at SpecEmitter.start's P; a turn that ends without one
      takes it from the end of its prompt records, or, with neither, from kv_len() once the stream has ended (the KV
      then holds exactly P rows: a record exists for every generated row the turn kept, ruling P2-R4).
    - Every record is checked against the prompt, the origin and the tokens handed out: a mismatch is a desync, and it
      raises rather than mislabel.
    - ReadoutTurn.off(header) is a turn whose request carried a nomos block with readouts off (steering or metadata
      only, review ruling R2). It takes no records; its summary says readout_off (ruling P2-R13)."""

    def __init__(self, layout: Layout | None, header: dict, prompt_ids, take=None, kv_len=None) -> None:
        if take is not None and kv_len is None:
            raise ValueError("the base path's takes need the engine's KV length (review ruling R9)")
        self.layout, self.header, self.take, self.kv_len = layout, header, take, kv_len
        self.prompt_ids = [int(t) for t in prompt_ids]
        self.P: int | None = None          # the turn's origin, once the engine has placed it
        self.gen: list[int] = []
        self.channels: list[str] = []
        self.records: list[dict] = []
        self.sent = 0
        self.header_sent = False
        self.first_prompt: int | None = None
        self.last_prompt = -1
        self.gen_records = 0
        self.counts = {"records": 0, "emotion_valid": 0, "workspace_valid": 0}

    @classmethod
    def off(cls, header: dict) -> "ReadoutTurn":
        """A turn with readouts off (review ruling R2): its final chunk carries only the summary."""
        return cls(None, header, [])

    def _set_origin(self, P: int, what: str) -> None:
        """Place the turn's origin at P, the KV's length once the prompt is in, as the engine reported it."""
        n, P = len(self.prompt_ids), int(P)
        if not n - 1 <= P <= n:
            raise ValueError(f"{what}: a {n}-token prompt fills {n} KV rows, or {n - 1} after a BOS strip, not {P}")
        if self.first_prompt is not None and not (P == n and self.last_prompt == n - 1):
            raise ValueError(f"{what}: the prompt records end at {self.last_prompt}, and the KV holds {P} prompt rows; "
                             "prompt records are sent only for a KV that holds the whole prompt")
        self.P = P

    def note_token(self, tok: int, channel: str) -> None:
        if self.take is not None:
            if not self.gen:                                       # the prefill ran: the KV holds the prompt
                self._set_origin(self.kv_len(), "the turn's origin")
                if self.P == len(self.prompt_ids):
                    self.add(*self.take(0, self.prompt_ids, False))
                else:
                    print(f"[readout] the KV holds {self.P} rows for a {len(self.prompt_ids)}-token prompt: "
                          "no prompt records", flush=True)
            else:
                self.add(*self.take(self.P + len(self.gen) - 1, [self.gen[-1]], True))   # the last step fed it
        self.gen.append(int(tok))
        self.channels.append(channel)

    def end(self) -> None:
        """The token source ran out without a stop token. On the base path its last step fed the last token: take it."""
        if self.take is not None and self.gen:
            self.add(*self.take(self.P + len(self.gen) - 1, [self.gen[-1]], True))

    def add(self, count: int, raw: bytes) -> None:
        if self.layout is None:
            raise ValueError("readout records arrived for a turn with readouts off")
        recs = np.frombuffer(raw, dtype=self.layout.dtype) if raw else np.zeros(0, self.layout.dtype)
        if len(recs) != count or len(raw) != count * self.layout.dtype.itemsize:
            raise ValueError(f"a readout frame of {len(raw)} bytes does not hold {count} records")
        for r in recs:
            self._add_one(r)

    def _add_one(self, r) -> None:
        pos, tok, flags = int(r["pos"]), int(r["token"]), int(r["flags"])
        prompt = int(r["source"]) == SOURCE_PROMPT
        if prompt:
            if (self.gen_records or pos >= len(self.prompt_ids) or (self.P is not None and pos >= self.P)
                    or pos <= self.last_prompt or (self.first_prompt is not None and pos != self.last_prompt + 1)):
                raise ValueError(f"prompt record at {pos} out of order")
            if tok != self.prompt_ids[pos]:
                raise ValueError(f"prompt record at {pos} carries token {tok}; the prompt has {self.prompt_ids[pos]}")
            if self.first_prompt is None:
                self.first_prompt = pos
            self.last_prompt = pos
            channel = None
        else:
            if self.P is None:                                     # the turn's first generated record: SpecEmitter.start's P
                self._set_origin(pos, f"generated record at {pos} out of order")
            j = pos - self.P
            if j != self.gen_records or j >= len(self.gen):
                raise ValueError(f"generated record at {pos} out of order")
            if tok != self.gen[j]:
                raise ValueError(f"generated record at {pos} carries token {tok}; the turn handed out {self.gen[j]}")
            channel = self.channels[j]
            self.gen_records += 1
        d = {"pos": pos, "token": tok, "source": "prompt" if prompt else "generated", "channel": channel,
             "emotion": None, "workspace": None, "missing": []}
        if flags & FLAG_EMO:
            d["emotion"] = {"proj": [[_g6(v) for v in row[:-1]] for row in r["emo"]],
                            "norm": [_g6(row[-1]) for row in r["emo"]]}
            self.counts["emotion_valid"] += 1
        else:
            d["missing"].append("emotion_unavailable")
        if self.layout.n_ws:
            if prompt:
                d["missing"].append("workspace_prompt_v1")
            elif flags & FLAG_WS:
                d["workspace"] = {"top": [[[int(i), _g6(s)] for i, s in zip(ids, sc)]
                                          for ids, sc in zip(r["ws_ids"], r["ws_scores"])]}
                self.counts["workspace_valid"] += 1
            else:
                d["missing"].append("workspace_unavailable")
        if prompt:
            d["missing"].append("channel_prompt_v1")
        self.records.append(d)
        self.counts["records"] += 1

    def payload(self) -> dict | None:
        """The `nomos` object for the next SSE chunk: the records that became final since the last one (with the header,
        the first time), or None when there are none."""
        if self.sent == len(self.records):
            return None
        out = {}
        if not self.header_sent:
            out["header"] = self.header
            self.header_sent = True
        out["readout"] = self.records[self.sent:]
        self.sent = len(self.records)
        return out

    def summary(self, saw_stop: bool) -> dict:
        if self.layout is None:                # readouts off (review ruling R2, ruling P2-R13): no record, no range
            return {"header": self.header,
                    "ranges": {"reused": [0, 0], "prompt": [0, 0], "generated": [0, 0]},
                    "counts": {"records": 0, "emotion_valid": 0, "workspace_valid": 0},
                    "missing": {"kv_reused": 0, "natural_stop": 1 if saw_stop else 0, "settle_trimmed": 0,
                                "readout_off": 1}}
        if self.P is None:                     # no generated record placed the origin
            if self.first_prompt is not None:
                self._set_origin(self.last_prompt + 1, "the turn's origin")
            elif self.kv_len is not None:
                self._set_origin(self.kv_len(), "the turn's origin")
            else:
                raise ValueError("the turn's origin is unknown: no record placed it and no KV length was given")
        if self.first_prompt is not None and self.last_prompt != self.P - 1:
            raise ValueError(f"the prompt records end at {self.last_prompt}, not at the prompt's last position")
        a = self.first_prompt if self.first_prompt is not None else self.P
        handed = len(self.gen) - (1 if saw_stop else 0)
        return {"header": self.header,
                "ranges": {"reused": [0, a], "prompt": [a, self.P], "generated": [self.P, self.P + self.gen_records]},
                "counts": dict(self.counts),
                "missing": {"kv_reused": a, "natural_stop": 1 if saw_stop else 0,
                            "settle_trimmed": handed - self.gen_records}}

    def final(self, saw_stop: bool) -> dict:
        """The last SSE chunk's `nomos` object: any records still pending, and the summary."""
        out = self.payload() or {}
        out["summary"] = self.summary(saw_stop)
        return out

    def complete(self, saw_stop: bool) -> dict:
        """The non-streaming reply's `nomos` object: the header, every record, and the summary."""
        return {"header": self.header, "readout": list(self.records), "summary": self.summary(saw_stop)}
