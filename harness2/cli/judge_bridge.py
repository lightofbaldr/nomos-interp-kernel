"""Serve OpenAI-compatible judge endpoints over `claude -p` and `codex exec`.

The upstream benchmark judges are ordinary OpenAI clients: JobBench calls chat completions,
LAB calls the Responses API. This bridge answers both from the locally installed CLIs on
their subscription logins, so the graders stay unchanged.

    harness2-judge-bridge            # prints HARNESS2_JUDGE_* lines for .env, then serves

Model ids: `cli/claude:<model>` (claude --model) and `cli/codex:<model>` (codex -m); the
`cli/` prefix is optional on the wire. Every prompt travels on the CLI's stdin. Images reach
Codex through `-i`; the Claude backend refuses them (422) so an image task is left unscored
rather than graded blind. A plan usage limit becomes 429 with Retry-After.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config
from ..core.clitools import (
    CliReply,
    claude_argv,
    claude_env,
    claude_system_args,
    codex_argv,
    codex_system_block,
    parse_claude_result,
    parse_codex_result,
    retry_after_seconds,
    strip_nested_env,
)
from .bridge import _endpoint

KNOWN_MODELS = ("cli/claude:opus", "cli/claude:sonnet", "cli/codex:gpt-5.6-sol")
_DATA_URL = re.compile(r"^data:(image/[\w.+-]+);base64,(.*)$", re.S)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif", "image/webp": "webp"}


class BridgeError(Exception):
    """An HTTP-level failure: status, message and an OpenAI-style error code."""

    def __init__(self, status: int, message: str, *, code: str = "bridge_error", retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class Backend:
    kind: str
    model: str


def parse_model_id(model: str | None) -> Backend:
    """`cli/claude:opus` -> Backend("claude", "opus"); anything else is a 400."""
    m = (model or "").strip()
    if m.startswith("cli/"):
        m = m[len("cli/") :]
    kind, sep, rest = m.partition(":")
    if kind not in ("claude", "codex") or not sep or not rest.strip():
        raise BridgeError(
            400,
            f"unknown model {model!r}; use cli/claude:<model> or cli/codex:<model>",
            code="model_not_found",
        )
    return Backend(kind, rest.strip())


@dataclass
class Request:
    """One judge call, in CLI terms."""

    system: str
    prompt: str
    images: list[tuple[str, bytes]] = field(default_factory=list)
    schema: dict | None = None
    effort: str = "low"


def _image_from_url(url: str, images: list[tuple[str, bytes]]) -> None:
    m = _DATA_URL.match(url or "")
    if not m:
        raise BridgeError(400, "only data: image URLs are supported", code="invalid_request_error")
    mime, payload = m.group(1), m.group(2)
    try:
        data = base64.b64decode(payload)
    except (ValueError, TypeError) as exc:
        raise BridgeError(400, f"bad image data URL: {exc}", code="invalid_request_error") from exc
    images.append((f"image{len(images) + 1}.{_EXT.get(mime, 'png')}", data))


def _content_text(content, images: list[tuple[str, bytes]]) -> str:
    """The text of a chat/Responses content value, collecting image parts on the way."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type", "")
        if ptype in ("text", "input_text", "output_text"):
            parts.append(str(part.get("text") or ""))
        elif ptype in ("image_url", "input_image"):
            raw = part.get("image_url")
            url = raw.get("url") if isinstance(raw, dict) else raw
            _image_from_url(str(url or ""), images)
    return "\n".join(p for p in parts if p)


def chat_request(body: dict, default_effort: str) -> Request:
    """messages -> one prompt; the JobBench judge sends system + user (text and images)."""
    if body.get("stream"):
        raise BridgeError(400, "streaming is not supported by the judge bridge", code="invalid_request_error")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise BridgeError(400, "messages must be a non-empty list", code="invalid_request_error")
    images: list[tuple[str, bytes]] = []
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role, text = m.get("role"), _content_text(m.get("content"), images)
        if role in ("system", "developer"):
            system_parts.append(text)
        elif role == "assistant":
            prompt_parts.append(f"[earlier assistant message]\n{text}")
        else:
            prompt_parts.append(text)
    schema = None
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") == "json_schema":
        schema = (rf.get("json_schema") or {}).get("schema") or rf.get("schema")
    effort = (
        body.get("reasoning_effort")
        or (body.get("extra_body") or {}).get("reasoning_effort")
        or default_effort
    )
    return Request("\n\n".join(system_parts), "\n\n".join(prompt_parts), images, schema, str(effort))


def responses_request(body: dict, default_effort: str) -> Request:
    """Responses API input -> one prompt; the LAB judge sends `input` plus a json_schema format."""
    if body.get("stream"):
        raise BridgeError(400, "streaming is not supported by the judge bridge", code="invalid_request_error")
    images: list[tuple[str, bytes]] = []
    system_parts = [str(body.get("instructions") or "")]
    prompt_parts: list[str] = []
    inp = body.get("input")
    if isinstance(inp, str):
        prompt_parts.append(inp)
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict) and "role" in item:
                text = _content_text(item.get("content"), images)
                (system_parts if item.get("role") in ("system", "developer") else prompt_parts).append(text)
            else:
                prompt_parts.append(_content_text(item, images))
    else:
        raise BridgeError(400, "input must be a string or a list", code="invalid_request_error")
    fmt = (body.get("text") or {}).get("format") or {}
    schema = fmt.get("schema") if isinstance(fmt, dict) and fmt.get("type") == "json_schema" else None
    effort = (body.get("reasoning") or {}).get("effort") or body.get("reasoning_effort") or default_effort
    system = "\n\n".join(p for p in system_parts if p)
    return Request(system, "\n\n".join(prompt_parts), images, schema, str(effort))


def require_images_supported(backend: Backend, req: Request) -> None:
    """Images reach Codex through `-i`; the Claude backend refuses them (422), so an
    image-bearing rubric is left unscored rather than graded blind."""
    if backend.kind == "claude" and req.images:
        raise BridgeError(
            422,
            "images are not supported on the claude backend; use cli/codex:<model> for "
            "image-bearing rubrics",
            code="unsupported_images",
        )


def strict_schema(schema):
    """The schema in the strict form `codex --output-schema` demands: every object carries
    `additionalProperties: false` and lists every property as required. Nested objects,
    array items and combinators are normalised too; non-object nodes pass through."""
    if isinstance(schema, list):
        return [strict_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) if k in ("items", "anyOf", "oneOf", "allOf", "not") else v for k, v in schema.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: strict_schema(v) for k, v in props.items()}
    if out.get("type") == "object" or isinstance(props, dict):
        out.setdefault("additionalProperties", False)
        if isinstance(props, dict):
            out["required"] = list(props.keys())
    for key in ("$defs", "definitions"):
        if isinstance(out.get(key), dict):
            out[key] = {k: strict_schema(v) for k, v in out[key].items()}
    return out


_CHILDREN: set[subprocess.Popen] = set()
_CHILDREN_LOCK = threading.Lock()


def register_child(proc: subprocess.Popen) -> None:
    """Track an in-flight CLI process so a bridge shutdown can end it."""
    with _CHILDREN_LOCK:
        _CHILDREN.add(proc)


def unregister_child(proc: subprocess.Popen) -> None:
    with _CHILDREN_LOCK:
        _CHILDREN.discard(proc)


def kill_children() -> int:
    """Terminate every in-flight CLI process; a stopped bridge must not leave `claude` or
    `codex` running (and billing) behind it. Returns how many were signalled."""
    with _CHILDREN_LOCK:
        procs = list(_CHILDREN)
        _CHILDREN.clear()
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return len(procs)


def _run_cli(argv, *, input: str, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    """`subprocess.run` with the child registered for `kill_children`."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
    )
    register_child(proc)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        unregister_child(proc)
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def run_backend(backend: Backend, req: Request, *, timeout: int, workdir: Path) -> CliReply:
    """One CLI call in a throwaway working directory. Prompt on stdin, never argv."""
    require_images_supported(backend, req)
    env = strip_nested_env(os.environ)
    workdir = Path(workdir)
    if backend.kind == "claude":
        env = claude_env(os.environ, isolation=config.CLAUDE_ISOLATION)
        system_arg, stdin = claude_system_args(req.system, req.prompt)
        argv = claude_argv(
            model=backend.model,
            effort=req.effort,
            system=system_arg,
            tools="",
            json_schema=json.dumps(req.schema) if req.schema else None,
            isolation=config.CLAUDE_ISOLATION,
        )
    else:
        last = workdir / "last_message.txt"
        schema_file = None
        if req.schema:
            schema_file = workdir / "schema.json"
            schema_file.write_text(json.dumps(strict_schema(req.schema)), encoding="utf-8")
        image_paths = []
        for name, data in req.images:
            p = workdir / name
            p.write_bytes(data)
            image_paths.append(p)
        argv = codex_argv(
            model=backend.model,
            effort=req.effort,
            cwd=workdir,
            last_message=last,
            sandbox="read-only",
            schema_file=schema_file,
            images=image_paths,
        )
        stdin = codex_system_block(req.system, req.prompt) if req.system else req.prompt
        # Codex keeps its shell even when judging, and a deliverable can carry a prompt
        # injection. The read-only sandbox blocks writes and network; pointing HOME at the
        # throwaway workdir keeps home-relative lookups away from real files, while auth
        # still resolves through the real CODEX_HOME.
        env.setdefault("CODEX_HOME", str(Path(os.environ.get("HOME") or Path.home()) / ".codex"))
        env["HOME"] = str(workdir)
    try:
        p = _run_cli(argv, input=stdin, cwd=workdir, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(504, f"{backend.kind} did not answer within {timeout}s", code="timeout") from exc
    if backend.kind == "claude":
        return parse_claude_result(p.stdout, p.returncode, p.stderr)
    return parse_codex_result(workdir / "last_message.txt", p.stdout, p.returncode, p.stderr)


def _tokens(reply: CliReply) -> tuple[int, int]:
    u = reply.usage or {}

    def _int(*keys):
        for k in keys:
            try:
                if u.get(k) is not None:
                    return int(u[k])
            except (TypeError, ValueError):
                pass
        return 0

    return _int("input_tokens", "input"), _int("output_tokens", "output")


def chat_response(model: str, reply: CliReply) -> dict:
    pt, ct = _tokens(reply)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply.text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }


def responses_response(model: str, reply: CliReply) -> dict:
    pt, ct = _tokens(reply)
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": reply.text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": pt, "output_tokens": ct, "total_tokens": pt + ct},
    }


class Bridge:
    """The server: a bounded pool of CLI calls behind a bearer key."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        key: str,
        concurrency: int,
        timeout: int,
        default_effort: str,
        log_dir: Path | None,
        usage_log: Path,
    ):
        self.host, self.port, self.key = host, int(port), key
        self.timeout = int(timeout)
        self.default_effort = default_effort
        self.log_dir = Path(log_dir) if log_dir else None
        self.usage_log = Path(usage_log)
        self._sem = threading.BoundedSemaphore(max(1, int(concurrency)))
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._server.bridge = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def shutdown(self) -> None:
        killed = kill_children()
        if killed:
            print(f"  [judge-bridge] ended {killed} in-flight CLI call(s)", flush=True)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def call(self, backend: Backend, req: Request, *, model: str) -> CliReply:
        require_images_supported(backend, req)
        t0 = time.time()
        with self._sem:
            workdir = Path(tempfile.mkdtemp(prefix="harness2-judge-"))
            try:
                reply = run_backend(backend, req, timeout=self.timeout, workdir=workdir)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        wall = round(time.time() - t0, 1)
        self._record(backend, model, req, reply, wall)
        if reply.ok:
            return reply
        if reply.limited:
            raise BridgeError(
                429, reply.error or "usage limit", code="rate_limit_exceeded",
                retry_after=retry_after_seconds(reply.error) or 60,
            )
        raise BridgeError(502, reply.error or "empty reply from the CLI", code="backend_error")

    def _record(self, backend: Backend, model: str, req: Request, reply: CliReply, wall: float) -> None:
        pt, ct = _tokens(reply)
        row = {
            "ts": int(time.time()),
            "backend": backend.kind,
            "model": model,
            "effort": req.effort,
            "input_tokens": pt,
            "output_tokens": ct,
            "cost_usd": reply.cost_usd,
            "wall_s": wall,
            "ok": reply.ok,
            "limited": reply.limited,
            "error": reply.error[:200],
        }
        with self._lock:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            if self.log_dir:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                stem = self.log_dir / f"{row['ts']}-{uuid.uuid4().hex[:8]}"
                stem.with_suffix(".prompt.md").write_text(
                    (f"# SYSTEM\n\n{req.system}\n\n# PROMPT\n\n" if req.system else "") + req.prompt,
                    encoding="utf-8",
                )
                stem.with_suffix(".response.md").write_text(reply.text or reply.error, encoding="utf-8")


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, fmt, *args):  # quiet: one line per call in the usage log instead
        return

    @property
    def bridge(self) -> Bridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def _send(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: BridgeError) -> None:
        headers = {"Retry-After": str(exc.retry_after)} if exc.status == 429 and exc.retry_after else None
        self._send(exc.status, {"error": {"message": exc.message, "type": exc.code, "code": exc.code}}, headers)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.bridge.key}"

    def do_GET(self):  # noqa: N802 (http.server naming)
        if not self._authorized():
            return self._error(BridgeError(401, "invalid bearer key", code="authentication_error"))
        if self.path.rstrip("/") == "/v1/models":
            data = [{"id": m, "object": "model", "owned_by": "harness2"} for m in KNOWN_MODELS]
            return self._send(200, {"object": "list", "data": data})
        return self._error(BridgeError(404, f"no such endpoint: {self.path}", code="not_found"))

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return self._error(BridgeError(401, "invalid bearer key", code="authentication_error"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(BridgeError(400, f"invalid JSON body: {exc}", code="invalid_request_error"))
        path = self.path.rstrip("/")
        try:
            if path == "/v1/chat/completions":
                req, shape = chat_request(body, self.bridge.default_effort), chat_response
            elif path == "/v1/responses":
                req, shape = responses_request(body, self.bridge.default_effort), responses_response
            else:
                raise BridgeError(404, f"no such endpoint: {self.path}", code="not_found")
            model = str(body.get("model") or "")
            reply = self.bridge.call(parse_model_id(model), req, model=model)
            self._send(200, shape(model, reply))
        except BridgeError as exc:
            self._error(exc)


def banner_lines(host: str, port: int, key: str) -> list[str]:
    """The .env lines an operator copies; the default judge is the one docs/PI.md recommends."""
    return [
        f"HARNESS2_JUDGE_BASE_URL=http://{host}:{port}/v1",
        f"HARNESS2_JUDGE_API_KEY={key}",
        "HARNESS2_JUDGE_MODEL=cli/claude:sonnet  # or cli/codex:<model> with HARNESS2_JUDGE_WORKERS=1",
    ]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="interface to bind")
    p.add_argument("--port", type=int, default=0, help="default: the saved port, else a free one")
    p.add_argument("--concurrency", type=int, default=6, help="concurrent CLI processes")
    p.add_argument("--timeout", type=int, default=600, help="seconds per judge call")
    p.add_argument("--effort", default=config.JUDGE_EFFORT, help="default reasoning effort")
    p.add_argument("--log-dir", type=Path, default=None, help="keep every prompt/response pair here")
    args = p.parse_args(argv)
    root = config.JUDGE_BRIDGE_DIR
    endpoint = _endpoint(root / "bridge.json", args.host, args.port)
    bridge = Bridge(
        host=args.host,
        port=endpoint["port"],
        key=endpoint["key"],
        concurrency=args.concurrency,
        timeout=args.timeout,
        default_effort=args.effort,
        log_dir=args.log_dir,
        usage_log=root / "usage.jsonl",
    )
    try:
        port = bridge.start()
    except OSError as exc:
        p.exit(1, f"cannot bind {args.host}:{endpoint['port']} ({exc}); pass --port to choose another\n")
    print("Add these to .env (the key and port are saved under dependencies/cli-bridge/):")
    for line in banner_lines(args.host, port, endpoint["key"]):
        print(line)
    print(f"serving judge calls (concurrency {args.concurrency}, effort {args.effort}); Ctrl-C stops", flush=True)

    def _stop(signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
