"""Narrow stdio MCP server: four capabilities, no shell, resources or fetch.

The server inherits no credentials. Its protocol, calls and responses are bounded;
the audit lives outside both capabilities. Mojo gets a separate retained copy.
"""

import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from .improver_files import read_file
from .login_tools_files import Files, parts, regular
from .sandbox import HOST_ENV, ProcessResult, Sandbox, SandboxTeardownError

TOOLS = ("list_dir", "read_file", "write_file", "run_mojo")
ALLOWED = tuple("mcp__harness2__" + name for name in TOOLS)
TERMINAL_TOOL = "EndConversation"  # Inventory-only exception approved in Prime 1619.
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_MESSAGE = 2 * 1024**2
MAX_CALLS = 256
MAX_PROBES = 16
OUTPUT_LIMIT = 64 * 1024


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=True, allow_nan=False, sort_keys=True) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_inventory(names):
    return (
        isinstance(names, list)
        and all(isinstance(x, str) for x in names)
        and len(names) == len(set(names))
        and set(names) in (set(ALLOWED), set(ALLOWED) | {TERMINAL_TOOL})
    )


def valid_nonce(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def bounded_result(result):
    """The shared runner may capture a final read chunk beyond its stop limit."""
    out, err = result.stdout.encode(), result.stderr.encode()
    if len(out) + len(err) <= OUTPUT_LIMIT:
        return result
    out = out[:OUTPUT_LIMIT]
    err = err[: OUTPUT_LIMIT - len(out)]
    return ProcessResult(
        result.returncode,
        out.decode(errors="ignore"),
        err.decode(errors="ignore"),
        result.seconds,
        "output_limit",
    )


class ProbeSandbox(Sandbox):
    """Same executor mounts/argv; additionally confirm service teardown."""

    def _stop_unit(self, unit):
        try:
            super()._stop_unit(unit)
            state = subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    unit + ".service",
                    "--property=LoadState,ActiveState,ControlGroup",
                ],
                env=HOST_ENV,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            fields = dict(line.split("=", 1) for line in state.stdout.splitlines() if "=" in line)
            if (
                state.returncode not in (0, 4)
                or fields.get("LoadState") not in ("loaded", "not-found")
                or fields.get("ActiveState") not in ("inactive", "failed")
                or fields.get("ControlGroup") != ""
            ):
                raise ValueError
        except (OSError, ValueError, subprocess.SubprocessError):
            raise SandboxTeardownError("Mojo probe teardown unconfirmed") from None


class Server:
    def __init__(self, context, workspace, audit, sandbox, *, nonce):
        if not valid_nonce(nonce):
            raise ValueError("invalid session nonce")
        self.audit = Path(audit)
        if self.audit.resolve() != self.audit.absolute():
            raise ValueError("linked audit root")
        for root in (context, workspace):
            if self.audit.is_relative_to(Path(root)) or Path(root).is_relative_to(self.audit):
                raise ValueError("tool audit and capabilities must be disjoint")
        self.files = Files(context, workspace)
        try:
            fd = os.open(
                self.audit / "calls.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            self.log = os.fdopen(fd, "wb")
        except BaseException:
            self.files.close()
            raise
        self.sandbox = sandbox
        self.nonce = nonce
        self.calls = self.probes = 0
        self.initialized = False
        self.closed = False
        self.append({"event": "open", "tools": list(TOOLS)})

    def append(self, record):
        self.log.write(encoded(record))
        self.log.flush()
        os.fsync(self.log.fileno())

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.append({"event": "close", "calls": self.calls, "probes": self.probes})
            finally:
                self.files.close()
                self.log.close()

    def run_mojo(self, path):
        bits = parts(path)
        if bits[0] != "workspace" or not path.endswith(".mojo"):
            raise ValueError("run_mojo requires workspace/... .mojo source")
        if self.probes >= MAX_PROBES:
            raise ValueError("Mojo probe budget exceeded")
        raw = self.files.read(path)
        raw.decode("utf-8")
        if b"\0" in raw:
            raise ValueError("NUL in Mojo source")
        self.probes += 1
        probe = Path(tempfile.mkdtemp(prefix="probe-", dir=self.audit))
        exclusive(probe / "solution.mojo", raw)
        # No broker, environment, argv, stdin or timeout is model-selectable.
        identity = self.sandbox.identity()
        compile_result = bounded_result(
            self.sandbox.run(
                probe,
                ["/opt/mojo/bin/mojo", "build", "/work/solution.mojo", "-o", "/work/program"],
                timeout=90,
                output_limit=OUTPUT_LIMIT,
            )
        )
        ran = None
        if not compile_result.reason and compile_result.returncode == 0:
            exe = probe / "program"
            regular(exe.lstat(), 64 * 1024**2)
            if not exe.lstat().st_mode & stat.S_IXUSR:
                raise ValueError("compiler did not produce an executable")
            ran = bounded_result(
                self.sandbox.run(probe, ["/work/program"], timeout=10, output_limit=OUTPUT_LIMIT)
            )
        if self.sandbox.identity() != identity:
            raise SandboxTeardownError("probe runtime changed; stop the stage")
        return {
            "path": path,
            "source_sha256": digest(raw),
            "probe": probe.name,
            "compile": asdict(compile_result),
            "run": asdict(ran) if ran else None,
            "runtime": identity,
        }

    def call(self, name, arguments):
        self.calls += 1
        # No contents in arguments audit; outputs are bounded and retained so a
        # refusal/probe exit is reviewable, not inferred from the model's prose.
        args_hash = digest(encoded(arguments))
        info = {"call": self.calls, "tool": name, "arguments_sha256": args_hash}
        if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
            info["path"] = arguments["path"][:2048]
        self.append(info | {"event": "call"})
        try:
            try:
                admitted = json.loads(read_file(self.audit / "admission.json"))
                if (
                    set(admitted) != {"tools", "nonce"}
                    or admitted["nonce"] != self.nonce
                    or not valid_inventory(admitted["tools"])
                ):
                    raise ValueError
            except (OSError, ValueError, TypeError):
                raise ValueError("tool inventory not admitted for this session") from None
            required = {"path", "content"} if name == "write_file" else {"path"}
            if self.calls > MAX_CALLS:
                raise ValueError("tool call budget exceeded")
            if name not in TOOLS or not isinstance(arguments, dict) or set(arguments) != required:
                raise ValueError("unknown tool or invalid arguments")
            if name == "run_mojo":
                out = self.run_mojo(**arguments)
            else:
                out = getattr(self.files, name)(**arguments)
            result = {
                "content": [{"type": "text", "text": json.dumps(out, allow_nan=False)}],
                "isError": False,
            }
        except (ValueError, OSError) as exc:
            # Do not echo absolute host paths from OS errors.
            reason = str(exc) if isinstance(exc, ValueError) else "file operation refused"
            result = {"content": [{"type": "text", "text": reason}], "isError": True}
        if len(encoded(result)) > MAX_MESSAGE:
            result = {
                "content": [{"type": "text", "text": "response budget exceeded"}],
                "isError": True,
            }
        # Reads already appear in the Claude transcript. Keep content out of the
        # audit except fixed-argv probe results and directory metadata.
        evidence = (
            result
            if name == "run_mojo"
            else {
                "isError": result["isError"],
                "response_sha256": digest(encoded(result)),
                "reason": result["content"][0]["text"] if result["isError"] else None,
            }
        )
        self.append(info | {"event": "result", "result": evidence})
        return result

    def dispatch(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("invalid JSON-RPC request")
        method = message.get("method")
        if "id" not in message:
            if method in ("notifications/initialized", "notifications/cancelled"):
                return None
            raise ValueError("unexpected notification")
        request_id = message["id"]
        if type(request_id) not in (str, int):
            raise ValueError("invalid request id")
        response = {"jsonrpc": "2.0", "id": request_id}
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("invalid request params")
        if method == "initialize" and not self.initialized:
            self.initialized = True
            version = params.get("protocolVersion")
            return response | {
                "result": {
                    "protocolVersion": version if version in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "harness2", "version": "1.0.0"},
                }
            }
        if not self.initialized:
            raise ValueError("initialize first")
        if method == "ping":
            return response | {"result": {}}
        if method == "tools/list":
            return response | {"result": {"tools": tool_schemas()}}
        if method == "tools/call":
            return response | {"result": self.call(params.get("name"), params.get("arguments", {}))}
        return response | {"error": {"code": -32601, "message": "method not supported"}}


def tool_schemas():
    descriptions = {
        "list_dir": "List one context/ or workspace/ directory; relative paths only.",
        "read_file": "Read a bounded UTF-8 file under context/ or workspace/ only.",
        "write_file": "Write UTF-8 text under workspace/ only; creates parents. Context is read-only.",
        "run_mojo": "Compile/run ONE self-contained workspace/*.mojo copy in bwrap; no network, "
        "no host home. Fixed build argv, 90s compile/10s run, 64KiB output each.",
    }
    out = []
    for name in TOOLS:
        props = {"path": {"type": "string"}}
        if name == "write_file":
            props["content"] = {"type": "string"}
        out.append(
            {
                "name": name,
                "description": descriptions[name],
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        )
    return out


def serve(server, stdin, stdout):
    try:
        for _ in range(1024):
            raw = stdin.readline(MAX_MESSAGE + 1)
            if not raw:
                return
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise ValueError("MCP message budget exceeded or incomplete line")
            # Reject nonstandard JSON values; duplicate members are ambiguous.
            message = json.loads(
                raw,
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            result = server.dispatch(message)
            if result is not None:
                stdout.write(encoded(result))
                stdout.flush()
        raise ValueError("MCP message count exceeded")
    finally:
        server.close()


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON member")
        out[key] = value
    return out


def main(config_path):
    import sys

    config = json.loads(read_file(Path(config_path)))
    sandbox = ProbeSandbox(mojo=Path(config["mojo"]))
    if sandbox.identity() != config["probe_identity"]:
        raise ValueError("probe runtime differs from launch identity")

    def stop(*_):
        # Unwind the probe's finally/stop handler before leaving the server.
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = Server(
        config["context"], config["workspace"], config["audit"], sandbox, nonce=config["nonce"]
    )
    serve(server, sys.stdin.buffer, sys.stdout.buffer)
