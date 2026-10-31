"""Whole-process Claude Code improver sandbox for the Mojo bench.

No host execution fallback. A fresh private home contains only an inert token;
the real login is read by the host gateway after explicit live authorization.
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
from .improver_broker import MAX_CALLS, MAX_TOKENS, PLACEHOLDER, ApiBroker, load_login
from .improver_files import plain_directory
from .sandbox import HOST_ENV, Sandbox, SandboxTeardownError

POLICY = "mojo-claude-process-v1"
ENTRY = Path(__file__).with_name("improver_entry.py")


class ImproverIdentityError(ValueError):
    """A changed runtime is a stop, not a reason for another model attempt."""


class ImproverSandbox(Sandbox):
    def __init__(self, *, claude, controls=None, **kwargs):
        super().__init__(**kwargs)
        self.claude = Path(claude).resolve(strict=True)
        # The supported install is the native Claude binary, not a host shell shim.
        with self.claude.open("rb") as file:
            if file.read(4) != b"\x7fELF":
                raise ValueError("improver needs the native Linux Claude binary")
        self.controls = plain_directory(controls) if controls is not None else None

    def identity(self):
        out = super().identity()
        files = {"claude": self.claude, "improver": Path(__file__), "improver_entry": ENTRY}
        for name in ("improver_broker", "improver_files"):
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
                "network": "api.anthropic.com:443 Messages/count_tokens only; host-owned OAuth",
                "authentication": "private Linux Claude login; no refresh or key fallback",
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
            "CLAUDE_CODE_OAUTH_TOKEN": PLACEHOLDER,
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
            command = ["/usr/bin/python3", "-I", "/runtime/improver-entry.py", "--", *command]
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


def runtime(*, controls=None):
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
        mojo=Path(os.environ.get("HARNESS2_MOJO_PREFIX", Path.home() / ".pixi/envs/mojo")),
        node=Path(shutil.which("node") or "/missing/node"),
        pi=pi,
    )


def runtime_identity():
    """No authentication read, invocation, model request or GPU access."""
    return runtime().identity()


def prepare_client(controls, spec, mission, system):
    """Immutable client inputs; shared with the native-client fake-API control."""
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
        isolation="flags",
        binary="/opt/claude/claude",
    )
    return command + [
        "--settings",
        "/runtime/settings.json",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--max-turns",
        str(spec.steps),
    ]


def run_isolated(cwd, spec, mission, system, wall, *, expected_identity=None):
    if os.environ.get("HARNESS2_MOJO_LIVE_APPROVED") != "1":
        raise ValueError("explicit live approval required for the improver")
    if spec.kind != "claude_cli" or spec.sandbox != "mojo-claude":
        raise ValueError("unsupported isolated improver channel")
    if config.CLAUDE_USE_VERTEX or config.CLAUDE_ISOLATION != "flags":
        raise ValueError(
            "isolated Mojo improver supports Claude login only, no provider/key fallback"
        )
    if type(wall) is not int or not 1 <= wall <= 3600:
        raise ValueError("improver timeout must be 1..3600 seconds")
    if type(spec.steps) is not int or not 1 <= spec.steps <= MAX_CALLS:
        raise ValueError("improver turns must be 1..128")
    cwd = plain_directory(cwd)
    with tempfile.TemporaryDirectory(prefix="h2-improver-controls-") as temp:
        controls = Path(temp)
        sandbox = runtime(controls=controls)
        identity = sandbox.identity()
        if expected_identity is not None and identity != expected_identity:
            raise ImproverIdentityError("improver runtime changed before launch")
        # No inherited settings, plugins or MCP servers. Hooks would still be
        # contained by the namespace, but disabling them also narrows behavior.
        command = prepare_client(controls, spec, mission, system)
        # Refuse early expiry; never refresh or copy the refresh token. No host
        # environment variables are forwarded into the namespace.
        login = load_login(Path.home() / ".claude/.credentials.json", min_ttl=wall + 30)
        with ApiBroker(controls / "api.sock", login, model=spec.model) as broker:
            result = sandbox.run(cwd, command, timeout=wall, broker=controls / "api.sock")
        if sandbox.identity() != identity:
            raise ImproverIdentityError("improver runtime changed during the attempt")
        # The client never had the credential; also refuse an accidental echo in
        # terminal output before any caller can print/audit/collect it.
        if login.token in result.stdout or login.token in result.stderr:
            raise ValueError("improver credential echo refused")
        reply = CliReply()
        if not result.reason:
            try:
                reply = parse_claude_result(result.stdout, result.returncode, result.stderr)
            except (ValueError, TypeError, AttributeError):
                reply = CliReply(error="invalid isolated CLI result")
        if result.reason or result.returncode:
            # Keep usable CLI accounting on unsuccessful exits, but never
            # adopt a killed/partial result that happens to contain success text.
            reply.text = ""
            reply.error = reply.error or (
                f"isolated improver rc={result.returncode}; "
                f"reason={result.reason or 'process failure'}"
            )
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
