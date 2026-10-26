"""Plain-output fixtures for the readout spine (review ruling R8): what the serve sends for a request without a `nomos`
block, captured from the code before Plan 2 and compared, byte for byte, after it (test_readout_serve.PlainOutput).
CPU only: the real FastAPI app and the real Muse tokenizer over scripted engines, with serve.py's clock frozen, so every
ID and timing field is fixed.

The cases: base and speculative decoding; stop and budget endings; reasoning, text and tool content; streaming and
non-streaming replies; a plain request on an engine an earlier request armed (armed_plain); and a plain request on a serve
started against an engine daemon that outlived an earlier serve (restart_plain). With new=True (Plan 2's code) that earlier
serve first steers the daemon; the code before Plan 2 cannot steer, so its capture has no earlier serve.

Capture, from the worktree root, against the branch point's pyhost (Plan 2, Task 5, Step 2):
  pixi run --no-install python3 tools/readout_plain_golden.py ROOT/pyhost pyhost/test_fixtures/readout_plain_sse.json BASE"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

import numpy as np

MUSE_TOK = os.path.expanduser("~/nomos_data/muse-glimmer-tokenizer")
FROZEN = 1767225600.25             # serve.py's clock: SSE ids chatcmpl-1767225600, non-streaming chatcmpl-1767225600250
MESSAGES = [{"role": "user", "content": "What is the capital of France?"}]
TEXT = (" to=self<|message|>Recall the capital.<|eom|>"
        "<|start|>assistant to=user<|message|>Paris.<|eot|>")
STEERED = (" to=self<|message|>Stay calm and recall it.<|eom|>"
           "<|start|>assistant to=user<|message|>Paris, calmly.<|eot|>")
TOOL = (" to=self<|message|>Use the weather tool.<|eom|>"
        "<|start|>assistant to=weather.get<|message|>"
        '<atem:function_calls>\n<atem:invoke name="weather.get">\n'
        '<atem:parameter name="city">Paris</atem:parameter>\n'
        '<atem:parameter name="days">3</atem:parameter>\n'
        "</atem:invoke>\n</atem:function_calls><|eot|>")
TOOLS = [{"type": "function", "function": {
    "name": "weather.get", "description": "The weather in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
                   "required": ["city"]}}}]
DIRS = "d" * 64                    # the direction set the earlier serve steers with (restart_plain, new=True)
CASES = (  # name, reply, speculative, max_tokens, tools, armed
    ("base_stop", TEXT, False, 1200, None, False),
    ("base_budget", TEXT, False, 9, None, False),
    ("spec_stop", TEXT, True, 1200, None, False),
    ("spec_budget", TEXT, True, 9, None, False),
    ("spec_tool", TOOL, True, 1200, TOOLS, False),
    ("armed_plain", TEXT, True, 1200, None, True),
)


def script_lm(host):
    """The scripted engine class, over whichever host module the serve imported."""

    class ScriptLM(host._GenMixin):
        """Says `out`, token by token: one-hot logits for the base path (host._GenMixin's own decode loop), and a
        speculative stream that hands out the same tokens, honouring the budget and the stop tokens. An armed engine's
        clear is recorded in `calls`."""

        def __init__(self, out):
            self.out, self.k, self.calls = list(out), 0, []
            self.V = max(self.out) + 1

        def _next(self):
            lg = np.zeros(self.V, dtype=np.float32)
            lg[self.out[min(self.k, len(self.out) - 1)]] = 1.0
            return lg

        def session_prepare(self, ids, spec=False):
            return 0

        def prefill(self, ids):
            self.k = 0
            return self._next()

        def step(self, tok):
            self.k += 1
            return self._next()

        def session_commit(self):
            return 0

        def cache_len(self):
            return 0

        def spec_generate_stream(self, ids, max_new_tokens=1024, stop_ids=(), vb=8):
            for tok in self.out[:max_new_tokens]:
                yield tok
                if tok in stop_ids:
                    return

        def steer_clear(self):
            self.calls.append("steer_clear")

        def readout_off(self):
            self.calls.append("readout_off")

    return ScriptLM


def engine(serve, tok, lm, spec, armed):
    """serve.Engine's attributes over a scripted engine. Armed: an earlier request left steering and readouts on (the
    code before Plan 2 has no such state, and ignores the attributes)."""
    protocol = serve.load_serve_protocol(tok, 2)
    return SimpleNamespace(
        tok=tok, protocol=protocol, model_name=protocol.default_model_name, lm=lm, stop=protocol.stop_ids,
        lock=threading.Lock(), spec_loaded=spec, spec_vb=8, proc=None, rep_penalty_default=1.0,
        kvstore={"status": "off", "dir": None}, readout_ok=True,
        ro_active={"directions": DIRS, "lens": None} if armed else None, steer_armed=armed)


def post(serve, eng, body: dict) -> dict:
    """POST /v1/chat/completions through the real app, with eng as its engine and serve.py's clock frozen: the reply as
    it went out (status, content type, body)."""
    from fastapi.testclient import TestClient
    previous = getattr(serve.app.state, "engine", None)
    serve.app.state.engine = eng
    try:
        with mock.patch.object(serve, "time", SimpleNamespace(time=lambda: FROZEN, monotonic=time.monotonic)), \
                contextlib.redirect_stdout(io.StringIO()):
            r = TestClient(serve.app).post("/v1/chat/completions", json=body)
    finally:
        if previous is None:
            del serve.app.state.engine
        else:
            serve.app.state.engine = previous
    return {"status": r.status_code, "type": r.headers["content-type"], "body": r.text}


class DaemonLM:
    """The engine behind a fake engine daemon, with the kernel's steering rules (nomos_ffi.mojo):
    - steer_set arms steering and leaves the live KV untracked (:3962-3982);
    - steer_clear disarms it, and leaves the live KV untracked only when it was armed (:4024-4037);
    - a prepare while steered bypasses the store and resets the KV (ledger R17, :3781-3788);
    - a prepare reuses the live KV only while it is tracked, up to the prompt's last token.
    It says `plain`, token by token, unless the turn's KV holds a row written while steered: then `steered`. `forwards`
    logs every forward as (kind, steered); `calls` logs the readout opcodes and each prepare."""

    def __init__(self, plain, steered):
        self.plain, self.steered_out = list(plain), list(steered)
        self.V = max(self.plain + self.steered_out) + 1
        self.kv = []                     # (token, written while steered), one per KV row
        self.tracked, self.steered, self.start = True, False, 0
        self.forwards, self.calls = [], []

    def model_id(self):
        return 2                          # Muse

    def _logits(self):
        out = self.steered_out if any(s for _, s in self.kv) else self.plain
        lg = np.zeros(self.V, dtype=np.float32)
        lg[out[min(len(self.kv) - self.start, len(out) - 1)]] = 1.0
        return lg

    def _write(self, kind, ids):
        self.forwards.append((kind, self.steered))
        self.kv += [(int(t), self.steered) for t in ids]
        return self._logits()

    def session_prepare(self, ids, spec=False):
        self.calls.append("prepare")
        c = 0
        if not self.steered and self.tracked:
            held = [t for t, _ in self.kv]
            while c < min(len(held), len(ids) - 1) and held[c] == ids[c]:
                c += 1
        self.kv, self.tracked = self.kv[:c], True
        return c

    def prefill(self, ids):
        self.kv, self.start = [], len(ids)
        return self._write("prefill", ids)

    def prefill_cont(self, ids):
        self.start = len(self.kv) + len(ids)
        return self._write("prefill_cont", ids)

    def step(self, tok):
        return self._write("step", [tok])

    def set_cache_len(self, n):
        del self.kv[n:]

    def cache_len(self):
        return len(self.kv)

    def session_commit(self):
        return 0

    def readout_off(self):
        self.calls.append("readout_off")

    def steer_set(self, layer, directions, name, strength, from_pos):
        self.calls.append("steer_set")
        self.steered, self.tracked = True, False

    def steer_clear(self):
        self.calls.append("steer_clear")
        if self.steered:
            self.tracked = False
        self.steered = False


@contextlib.contextmanager
def daemon(engine_daemon, lm, root):
    """engine_daemon._serve_conn(lm) behind a UNIX socket in root, one connection at a time as engine_daemon.main serves
    them, until the block ends. Yields the socket's path."""
    path = os.path.join(root, "engine.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    srv.settimeout(0.2)
    done = threading.Event()

    def run():
        while not done.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            with conn:
                try:
                    engine_daemon._serve_conn(conn, lm)
                except OSError:
                    pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield path
    finally:
        done.set()
        t.join(10)
        srv.close()


def registry(root) -> str:
    """A registry with one direction set, DIRS, for the earlier serve's steering."""
    path = os.path.join(root, "registry.json")
    with open(path, "w") as f:
        json.dump({"v": 1, "assets": [
            {"id": DIRS, "kind": "directions", "dir": root, "D": 8, "layers": list(range(52)), "E": 3,
             "names": ["calm", "joyful", "afraid"], "layer_sizes": [70.0] * 52, "source": "s",
             "source_sha256": "0" * 64}]}, f)
    return path


def restart_case(new: bool, tok=None):
    """restart_plain. Returns (its replies, the daemon's DaemonLM, (len(calls), len(forwards)) where the last serve
    starts)."""
    import engine_daemon
    import serve
    if tok is None:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(MUSE_TOK)
    dlm = DaemonLM(tok(TEXT, add_special_tokens=False).input_ids, tok(STEERED, add_special_tokens=False).input_ids)
    plain = {"messages": MESSAGES, "temperature": 0, "max_tokens": 1200, "stream": True}

    def start():
        with contextlib.redirect_stdout(io.StringIO()):
            return serve.Engine()

    with tempfile.TemporaryDirectory() as root, daemon(engine_daemon, dlm, root) as path, \
            mock.patch.dict(os.environ, {"NOMOS_ENGINE_SOCKET": path, "NOMOS_KVSTORE": "0", "NOMOS_SERVE_SPEC": "0",
                                         "NOMOS_VISION_SO": "", "NOMOS_DEBUG_RAW": "",
                                         "NOMOS_READOUT_REGISTRY": registry(root)}), \
            mock.patch.object(serve.H, "TOK_DIR", MUSE_TOK):
        if new:
            first = start()
            post(serve, first, {**plain, "nomos": {"steer": {"layer": 24, "directions": DIRS, "direction": "calm",
                                                             "strength": 0.7}}})
            first.lm.shutdown()           # that serve goes away; the daemon stays, steered
        mark = (len(dlm.calls), len(dlm.forwards))
        last = start()
        replies = {"stream": post(serve, last, plain)}
        last.lm.shutdown()
    return replies, dlm, mark


def render(new: bool = False) -> dict:
    """Every case's replies, through the serve that `import serve` finds (sys.path decides which)."""
    import host
    import serve
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MUSE_TOK)
    ScriptLM = script_lm(host)
    out = {}
    with mock.patch.dict(os.environ, {"NOMOS_DEBUG_RAW": ""}):
        for name, reply, spec, max_tokens, tools, armed in CASES:
            out[name] = {}
            for stream in (True, False):
                lm = ScriptLM(tok(reply, add_special_tokens=False).input_ids)
                body = {"messages": MESSAGES, "temperature": 0, "max_tokens": max_tokens, "stream": stream}
                if tools:
                    body["tools"] = tools
                out[name]["stream" if stream else "nonstream"] = post(serve, engine(serve, tok, lm, spec, armed), body)
    out["restart_plain"] = restart_case(new, tok)[0]
    return out


def main(pyhost: str, out_path: str, base: str) -> None:
    sys.path.insert(0, os.path.abspath(pyhost))
    cases = render(new=False)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path + ".tmp", "w") as f:
        json.dump({"v": 1, "base": base, "cases": cases}, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(out_path + ".tmp", out_path)
    print(f"wrote {len(cases)} cases to {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:])
