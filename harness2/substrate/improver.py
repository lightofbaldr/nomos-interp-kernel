"""Whole-process Claude Code improver sandbox for the Mojo bench.

No fallback from a selected sandbox to host execution. Subscription pass-through
is code-held over tool-visible token exposure; protocol tests use fakes only.
Compiler/Pi probes, native tools, hooks and subprocesses all remain sandboxed.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config
from ..core.clitools import CliReply, claude_argv, claude_system_args, parse_claude_result
from .improver_auth import MODES, Credentials
from .improver_broker import MAX_CALLS, MAX_TOKENS, ApiBroker
from .improver_files import plain_directory
from .sandbox import HOST_ENV, Sandbox, SandboxTeardownError

POLICY = "mojo-claude-process-v2"
ENTRY = Path(__file__).with_name("improver_entry.py")
# Deliberately not an environment option. Changing this requires a reviewed
# trust decision, not a caller carrying an executor's existing live flag.
SUBSCRIPTION_TOOLS_REVIEWED = False


class ImproverIdentityError(ValueError):
    """A changed runtime is a stop, not a reason for another model attempt."""


class ImproverSandbox(Sandbox):
    def __init__(self, *, claude, controls=None, auth_mode="api-key", **kwargs):
        if auth_mode not in MODES:
            raise ValueError("invalid improver auth mode")
        super().__init__(**kwargs)
        self.auth_mode = auth_mode
        self.claude = Path(claude).resolve(strict=True)
        # The supported install is the native Claude binary, not a host shell shim.
        with self.claude.open("rb") as file:
            if file.read(4) != b"\x7fELF":
                raise ValueError("improver needs the native Linux Claude binary")
        self.controls = plain_directory(controls) if controls is not None else None

    def identity(self):
        out = super().identity()
        files = {"claude": self.claude, "improver": Path(__file__), "improver_entry": ENTRY}
        for name in ("improver_broker", "improver_files", "improver_audit", "improver_auth"):
            files[name] = Path(__file__).with_name(name + ".py")
        for name in ("clitools", "proposer", "spec"):
            files[name] = Path(__file__).parents[1] / f"core/{name}.py"
        files["model_broker_server"] = Path(__file__).with_name("model_broker.py")
        for name, path in files.items():
            with path.open("rb") as file:
                out["files"][name] = hashlib.file_digest(file, "sha256").hexdigest()
        out.update(
            {
                "policy": POLICY,
                "network": "api.anthropic.com:443 Messages/count_tokens only",
                "authentication": self.auth_mode,
                "subscription_tools_reviewed": SUBSCRIPTION_TOOLS_REVIEWED,
                "audit": "retained private scratch; bounded stream-json stdout/stderr",
                "max_api_calls": MAX_CALLS,
                "max_output_tokens_per_call": MAX_TOKENS,
                "cpu_quota": "200%",
                "swap_bytes": 0,
                "file_bytes": 67108864,
                "file_descriptors": 256,
                "wall_limit_seconds": 3600,
                "search": "API-backed search only; no direct WebFetch/downloads",
            }
        )
        return out

    def argv(self, workspace, command, *, broker=None):
        args = super().argv(workspace, [], broker=None)
        if args[-3:] != ["--remount-ro", "/", "--"]:
            raise ValueError("base sandbox command contract changed")
        args = args[:-3]
        args += ["--ro-bind", str(self.claude), "/opt/claude/claude"]
        env = {
            "PATH": "/opt/claude:/opt/mojo/bin:/opt/node:/usr/bin:/bin",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CONFIG_DIR": "/work/.home/.claude",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8092",
        }
        for key, value in env.items():
            args += ["--setenv", key, value]
        if broker is not None:
            if self.controls is None:
                raise ValueError("missing immutable improver controls")
            for name in ("mission.txt", "settings.json"):
                path = self.controls / name
                if path.resolve() != path or not path.is_file():
                    raise ValueError("linked or missing improver control")
                args += ["--ro-bind", str(path), "/runtime/" + name]
            args += [
                "--ro-bind",
                str(broker),
                "/improver.sock",
                "--ro-bind",
                str(ENTRY),
                "/runtime/improver-entry.py",
            ]
            command = [
                "/usr/bin/python3",
                "-I",
                "/runtime/improver-entry.py",
                "--auth",
                self.auth_mode,
                "--",
                *command,
            ]
        args += ["--remount-ro", "/", "--", *command]
        return args

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
                or fields.get("ActiveState") not in ("inactive", "failed")
                or fields.get("LoadState") not in ("loaded", "not-found")
                or fields.get("ControlGroup") != ""
            ):
                raise ValueError
        except (OSError, ValueError, subprocess.SubprocessError):
            raise SandboxTeardownError(
                "improver service teardown unconfirmed; stop and reconcile"
            ) from None

    def run(self, *args, **kwargs):
        try:
            return super().run(*args, **kwargs)
        except (OSError, subprocess.SubprocessError):
            # In particular, do not turn a failed wait/stop into a retry while
            # a descendant could still be writing the output being collected.
            raise SandboxTeardownError("improver supervision failed; stop and reconcile") from None


def runtime(*, controls=None, auth_mode="api-key"):
    binary = shutil.which("claude")
    if not binary:
        raise ValueError("native Claude Code not installed; no host fallback")
    pi_path = os.environ.get("HARNESS2_MOJO_PI_PACKAGE")
    if not pi_path:
        pi_binary = shutil.which("pi")
        if not pi_binary:
            raise ValueError("Pi toolchain missing; no host fallback")
        pi_path = Path(pi_binary).resolve().parents[2]
    pi = Path(pi_path)
    return ImproverSandbox(
        claude=Path(binary),
        controls=controls,
        auth_mode=auth_mode,
        mojo=Path(os.environ.get("HARNESS2_MOJO_PREFIX", Path.home() / ".pixi/envs/mojo")),
        node=Path(shutil.which("node") or "/missing/node"),
        pi=pi,
    )


def runtime_identity(*, auth_mode):
    """No authentication read, invocation, model request or GPU access."""
    return runtime(auth_mode=auth_mode).identity()


def prepare_client(controls, spec, mission, system):
    """Immutable client inputs; shared with the native-client fake-API control."""
    if spec.auth_mode not in MODES:
        raise ValueError("explicit client auth mode required")
    sys_arg, stdin = claude_system_args(system, mission)
    (controls / "mission.txt").write_text(stdin)
    settings = {
        "disableAllHooks": True,
        "enabledPlugins": {},
        "env": {
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(MAX_TOKENS),
        },
    }
    # An alias in DEFAULT_OPUS_MODEL would become a literal upstream model ID
    # ("opus"), bypassing Claude's alias resolution. Leave aliases to the client;
    # the broker still refuses any different family, including auxiliary calls.
    if spec.model.startswith("claude-"):
        settings["env"].update(
            {
                key: spec.model
                for key in (
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL",
                    "ANTHROPIC_SMALL_FAST_MODEL",
                )
            }
        )
    (controls / "settings.json").write_text(json.dumps(settings))
    command = claude_argv(
        model=spec.model,
        effort=spec.effort,
        system=sys_arg,
        skip_permissions=True,
        isolation="flags" if spec.auth_mode == "subscription" else "bare",
        binary="/opt/claude/claude",
    )
    command[command.index("--output-format") + 1] = "stream-json"
    return command + [
        "--verbose",
        "--settings",
        "/runtime/settings.json",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--max-turns",
        str(spec.steps),
    ]


def captured_reply(result):
    """Parse a bounded stream-json capture, never accept a partial/plain stream.

    Called only after the credential scrub in the live route; also
    used directly by auth-independent fake-client controls.
    """
    reply = CliReply(error="invalid or incomplete isolated CLI stream")
    try:
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if (
            events
            and all(isinstance(event, dict) for event in events)
            and events[-1].get("type") == "result"
            and isinstance(events[-1].get("subtype"), str)
        ):
            reply = parse_claude_result(json.dumps(events[-1]), result.returncode, result.stderr)
    except (ValueError, TypeError, AttributeError):
        pass
    if result.reason or result.returncode:
        # Keep usable accounting on failed exits, never adopt killed/partial
        # output even if the last captured event happens to be a result.
        reply.text = ""
        reply.error = reply.error or (
            f"isolated improver rc={result.returncode}; reason={result.reason or 'process failure'}"
        )
    reply.transcript = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "reason": result.reason,
        "seconds": result.seconds,
        "teardown": "confirmed",
    }
    return reply


def run_isolated(cwd, spec, mission, system, wall, *, expected_identity=None):
    if os.environ.get("HARNESS2_MOJO_LIVE_APPROVED") != "1":
        raise ValueError("explicit live approval required for the improver")
    if spec.kind != "claude_cli" or spec.sandbox != "mojo-claude":
        raise ValueError("unsupported isolated improver channel")
    if spec.auth_mode == "subscription" and not SUBSCRIPTION_TOOLS_REVIEWED:
        raise ImproverIdentityError("subscription live route on hold: tool-visible token")
    if config.CLAUDE_USE_VERTEX:
        raise ValueError("isolated Mojo improver supports Anthropic only; no provider fallback")
    if type(wall) is not int or not 1 <= wall <= 3600:
        raise ValueError("improver timeout must be 1..3600 seconds")
    if type(spec.steps) is not int or not 1 <= spec.steps <= MAX_CALLS:
        raise ValueError("improver turns must be 1..128")
    auth = Credentials.from_env(spec.auth_mode)
    cwd = plain_directory(cwd)
    with tempfile.TemporaryDirectory(prefix="h2-improver-controls-") as temp:
        controls = Path(temp)
        sandbox = runtime(controls=controls, auth_mode=spec.auth_mode)
        identity = sandbox.identity()
        if expected_identity is not None and identity != expected_identity:
            raise ImproverIdentityError("improver runtime changed before launch")
        # No inherited settings, plugins or MCP servers. Hooks would still be
        # contained by the namespace, but disabling them also narrows behavior.
        command = prepare_client(controls, spec, mission, system)
        with ApiBroker(
            controls / "api.sock", mode=auth.mode, api_key=auth.broker_key(), model=spec.model
        ) as broker:
            result = sandbox.run(
                cwd,
                command,
                timeout=wall,
                broker=controls / "api.sock",
                input_data=auth.client_input(),
            )
        if sandbox.identity() != identity:
            raise ImproverIdentityError("improver runtime changed during the attempt")
        # A literal echo makes the attempt fail, with scrubbed diagnostics. This
        # does not assert that arbitrary model-authored scratch is secret-free.
        result = auth.scrub(result)
        reply = captured_reply(result)
        if (
            broker.last_error
            and broker.last_error["reason"] == "improver model call budget exhausted"
        ):
            reply.limited = False  # A local cap is not a provider reset to wait out.
        reply.usage["sandbox"] = {
            "identity": identity,
            "model_calls": broker.calls,
            "broker_error": broker.last_error,
            "returncode": result.returncode,
            "reason": result.reason,
            "seconds": result.seconds,
            "teardown": "confirmed",
        }
        return reply
