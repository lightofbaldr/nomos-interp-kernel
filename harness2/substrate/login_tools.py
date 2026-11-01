"""Host Claude login, restricted to one MCP server. NOT a process sandbox.

No credential is read, copied, relayed or supplied by harness2. The host CLI uses
its own stored login. Its tool policy, not an OS boundary, protects host files.
Only model-authored Mojo probes execute inside bubblewrap.
"""

import hashlib
import json
import os
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..core.clitools import claude_argv, claude_system_args
from .improver import ImproverIdentityError, captured_reply
from .improver_files import plain_directory, read_file
from .login_tools_server import (
    ALLOWED,
    TERMINAL_TOOL,
    ProbeSandbox,
    digest,
    encoded,
    exclusive,
    valid_inventory,
    valid_nonce,
)
from .sandbox import HOST_ENV, ProcessResult, SandboxTeardownError

POLICY = "host-cc-login-tools-v1"
ENTRY = Path(__file__).with_name("login_tools_entry.py").resolve()
MAX_OUTPUT = 4 * 1024**2


class TerminalStop(ValueError):
    """EndConversation is never a successful stage, including text stages."""


BOUNDARY = """Environment: host Claude Code with no built-in tools. Use only
mcp__harness2__list_dir, read_file, write_file and run_mojo. No Bash, shell,
web, URL fetches, downloads, other MCP servers or host tools. Relative tool
paths start with context/ (read-only evidence) or workspace/ (writable).
The candidate harness is workspace/harness/, copied from context/harness/.
Never write context/. Write small self-contained .mojo probes to workspace/;
run_mojo compiles and runs a private copy in bwrap, without network or host home.
It takes no shell commands, flags or environment. There is no tool to delete
files; use empty text to clear an optional advisory file. Describe unavailable
operations rather than inventing a successful check. This is a restricted-tool
policy for the HOST client, not whole-process OS isolation.
"""


def environment():
    # Whitelist instead of inheriting session API keys, token overrides, proxy
    # settings, executable hooks, provider switches or debugging destinations.
    return {
        **HOST_ENV,
        "HOME": str(Path.home()),
        "TERM": "dumb",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
    }


def runtime():
    binary = shutil.which("claude")
    if not binary:
        raise ValueError("Claude Code missing; no host fallback")
    return Path(binary).resolve(strict=True), ProbeSandbox(
        mojo=Path(os.environ.get("HARNESS2_MOJO_PREFIX", Path.home() / ".pixi/envs/mojo"))
    )


def runtime_identity():
    """Read/hash executable and code only: no login read, invoke or request."""
    binary, probe = runtime()
    files = {"claude": binary, "python": Path(sys.executable).resolve()}
    files.update(
        {
            name: Path(__file__).with_name(name)
            for name in (
                "login_tools.py",
                "login_tools_entry.py",
                "login_tools_server.py",
                "login_tools_files.py",
                "improver_files.py",
                "improver_audit.py",
                "improver.py",
            )
        }
    )
    files.update(
        {
            "core/" + name: Path(__file__).parents[1] / "core" / name
            for name in ("proposer.py", "clitools.py", "spec.py")
        }
    )
    hashes = {}
    for name, path in files.items():
        with path.open("rb") as file:
            hashes[name] = hashlib.file_digest(file, "sha256").hexdigest()
    return {
        "policy": POLICY,
        "files": hashes,
        "probe": probe.identity(),
        "allowed_tools": list(ALLOWED),
        "client": "host login, tool policy only",
    }


def login_status(binary, work):
    """Ask the CLI for metadata, never inspect its credential storage ourselves.

    Refuse a console/API login as well as inherited-key overrides. Only a normal
    first-party Claude subscription login is authorized for this route.
    """
    result = subprocess.run(
        [str(binary), "auth", "status"],
        cwd=work,
        env=environment(),
        text=True,
        capture_output=True,
        timeout=20,
    )
    try:
        doc = json.loads(result.stdout) if len(result.stdout) <= 65536 else {}
        if (
            result.returncode != 0
            or doc.get("loggedIn") is not True
            or doc.get("authMethod") != "claude.ai"
        ):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("normal Claude subscription login not confirmed") from None
    return {"loggedIn": True, "authMethod": "claude.ai"}


def prepare(work, spec, mission, system, identity):
    if spec.sandbox != "login-tools" or spec.auth_mode != "off":
        raise ValueError("not a login-tools channel")
    work = plain_directory(work)
    controls = work.parent / "login-tools"
    controls.mkdir(mode=0o700)  # one fresh control directory per retained attempt
    context = plain_directory(work / "context")
    workspace = plain_directory(work / "workspace")
    binary, probe = runtime()
    cfg = {
        "context": str(context),
        "workspace": str(workspace),
        "audit": str(controls),
        "mojo": str(probe.mojo),
        "probe_identity": identity["probe"],
        "nonce": secrets.token_hex(32),
    }
    exclusive(controls / "server.json", encoded(cfg))
    # MCP env entries alone merge with the client's environment. env -i is the
    # boundary that actually clears it, including any credential CC adds itself.
    mcp_env = {**HOST_ENV, "PYTHONDONTWRITEBYTECODE": "1"}
    mcp = {
        "mcpServers": {
            "harness2": {
                "type": "stdio",
                "command": "/usr/bin/env",
                "args": [
                    "-i",
                    *(f"{k}={v}" for k, v in sorted(mcp_env.items())),
                    str(Path(sys.executable).resolve()),
                    "-I",
                    "-B",
                    str(ENTRY),
                    str(controls / "server.json"),
                ],
                "env": {},
            }
        }
    }
    exclusive(controls / "mcp.json", encoded(mcp))
    settings = {
        "disableAllHooks": True,
        "enabledPlugins": {},
        "permissions": {"allow": list(ALLOWED), "defaultMode": "dontAsk"},
    }
    exclusive(controls / "settings.json", encoded(settings))
    effective_mission = mission.replace("context/harness", "workspace/harness")
    effective_system = system.replace("context/harness", "workspace/harness")
    system_arg, stdin = claude_system_args(BOUNDARY + "\n" + effective_system, effective_mission)
    exclusive(controls / "mission.txt", stdin.encode())
    cmd = claude_argv(
        model=spec.model,
        effort=spec.effort,
        system=system_arg,
        tools="",
        isolation="flags",
        binary=str(binary),
    )
    cmd[cmd.index("--output-format") + 1] = "stream-json"
    cmd += [
        "--verbose",
        "--restricted",
        "--no-chrome",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--mcp-config",
        str(controls / "mcp.json"),
        "--allowedTools",
        ",".join(ALLOWED),
        "--settings",
        str(controls / "settings.json"),
        "--max-turns",
        str(spec.steps),
    ]
    exclusive(
        controls / "launch.json",
        encoded({"argv": cmd, "identity": identity, "credential_source": "CLI's stored login"}),
    )
    return controls, cmd


def publish_admission(controls, tools, nonce):
    """One atomic 0600 admission, no overwrite; retain a separate audit receipt."""
    if not valid_nonce(nonce) or not valid_inventory(tools):
        raise ValueError("invalid admission")
    raw = encoded({"tools": tools, "nonce": nonce})
    exclusive(controls / "admission-record.json", raw)
    fd, name = tempfile.mkstemp(prefix=".admit-", dir=controls)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.link(name, controls / "admission.json")
    finally:
        os.unlink(name)
    directory = os.open(controls, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run_client(command, work, mission_path, *, nonce, timeout):
    """Bounded host capture. TERM lets MCP unwind any sandbox probe before KILL.

    This is the approved stdout-init fallback, NOT strict held-input ordering:
    the mission is already on stdin. Our MCP calls additionally require the
    parent-owned admission file, written only after a valid inventory. CLI
    built-in suppression remains a CLI-policy guarantee, not OS isolation.

    A surviving group or incomplete probe audit is fatal; never harvest/retry
    while a tool may still be active. No detached host subprocess is offered.
    """
    if type(timeout) is not int or not 1 <= timeout <= 3600 or not valid_nonce(nonce):
        raise ValueError("invalid client timeout")
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    start = time.monotonic()
    reason = None
    pending = bytearray()
    inventories = []
    admitted = False
    with mission_path.open("rb") as stdin:
        proc = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=environment(),
            start_new_session=True,
        )
    try:
        with selectors.DefaultSelector() as sel:
            for name in chunks:
                sel.register(getattr(proc, name), selectors.EVENT_READ, name)
            while sel.get_map():
                if time.monotonic() - start > timeout:
                    reason = "timeout"
                    break
                for key, _ in sel.select(0.1):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    if sum(map(len, chunks.values())) + len(data) > MAX_OUTPUT:
                        reason = "output_limit"
                        break
                    chunks[key.data].extend(data)
                    if key.data == "stdout":
                        pending.extend(data)
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            try:
                                event = json.loads(line)
                                if (
                                    isinstance(event, dict)
                                    and event.get("type") == "system"
                                    and event.get("subtype") == "init"
                                ):
                                    inventories.append(event.get("tools"))
                                check_event(event)
                                if (
                                    isinstance(event, dict)
                                    and event.get("type") == "system"
                                    and event.get("subtype") == "init"
                                ):
                                    if admitted:
                                        raise ValueError("repeated inventory")
                                    publish_admission(mission_path.parent, event["tools"], nonce)
                                    admitted = True
                            except TerminalStop:
                                reason = "end_conversation"
                                break
                            except (OSError, ValueError, TypeError):
                                reason = "tool_policy"
                                break
                if reason:
                    break
        if reason is None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                reason = "timeout"
    finally:
        try:
            # Owned control file only; revoke on EVERY exit before process stop.
            # The immutable receipt is outside the file capabilities as well.
            (mission_path.parent / "admission.json").unlink(missing_ok=True)
            exclusive(
                mission_path.parent / "inventory.json",
                encoded(
                    {
                        "observed": inventories,
                        "admitted": admitted,
                        "revoked": True,
                        "status": "observed" if inventories else "unknown",
                        "stop_reason": reason,
                        "ordering": "stdin-text supplied before stdout init; MCP admission barrier",
                    }
                ),
            )
        finally:
            stop_client(proc)
    return ProcessResult(
        proc.returncode,
        chunks["stdout"].decode(errors="replace"),
        chunks["stderr"].decode(errors="replace"),
        time.monotonic() - start,
        reason,
    )


def stop_client(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=25)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    proc.stdout.close()
    proc.stderr.close()
    # The group leader can exit before a child. Allow the MCP finally to stop
    # its supervised probe; refuse on unconfirmed group teardown.
    deadline = time.monotonic() + 25
    while True:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            os.killpg(proc.pid, signal.SIGKILL)
            raise SandboxTeardownError("host CLI group teardown unconfirmed")
        time.sleep(0.05)


def check_event(event):
    """Reject widened tool inventories/calls, without assuming CLI flag success."""
    if not isinstance(event, dict):
        raise ValueError("invalid CLI event")
    if event.get("type") == "system" and event.get("subtype") == "init":
        names = event.get("tools")
        if not valid_inventory(names):
            raise ValueError("CLI did not expose exactly the four harness2 tools")
    message = event.get("message", {})
    if isinstance(message, dict):
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if block.get("name") == TERMINAL_TOOL:
                    raise TerminalStop("end_conversation")
                if block.get("name") not in ALLOWED:
                    raise ValueError("CLI attempted a tool outside its allowlist")


def check_stream(stdout):
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    for event in events:
        check_event(event)
    if (
        sum(event.get("type") == "system" and event.get("subtype") == "init" for event in events)
        != 1
    ):
        raise ValueError("missing or repeated CLI tool inventory")


def read_calls(controls):
    raw = read_file(controls / "calls.jsonl", max_bytes=16 * 1024**2)
    records = [json.loads(line) for line in raw.splitlines()]
    if not records or records[0].get("event") != "open" or records[-1].get("event") != "close":
        raise SandboxTeardownError("tool server did not close its audit")
    calls = [v for v in records if v.get("event") == "call"]
    results = [v for v in records if v.get("event") == "result"]
    if [v["call"] for v in calls] != list(range(1, len(calls) + 1)) or (
        [v["call"] for v in results] != [v["call"] for v in calls]
    ):
        raise SandboxTeardownError("tool audit has an unsettled call")
    return records


def bind_session(controls):
    names = (
        "server.json",
        "mcp.json",
        "settings.json",
        "mission.txt",
        "launch.json",
        "calls.jsonl",
        "login-status.json",
        "admission-record.json",
        "inventory.json",
        "reply-audit.json",
    )
    bindings = {}
    for name in names:
        if (controls / name).exists():
            bindings[name] = digest(read_file(controls / name, max_bytes=16 * 1024**2))
    exclusive(controls / "session.json", encoded({"policy": POLICY, "bindings": bindings}))


def verify_session(controls, *, accepted=False):
    raw = read_file(controls / "session.json")
    doc = json.loads(raw)
    expected = {"server.json", "mcp.json", "settings.json", "mission.txt", "launch.json"}
    bindings = doc.get("bindings", {})
    optional = {
        "calls.jsonl",
        "login-status.json",
        "admission-record.json",
        "inventory.json",
        "reply-audit.json",
    }
    if (
        doc.get("policy") != POLICY
        or not expected <= set(bindings)
        or not set(bindings) <= expected | optional
    ):
        raise ValueError("invalid login-tools session bindings")
    for name, sha in bindings.items():
        if digest(read_file(controls / name, max_bytes=16 * 1024**2)) != sha:
            raise ValueError("login-tools audit changed")
    if accepted:
        if set(bindings) != expected | optional:
            raise ValueError("incomplete accepted login-tools audit")
        status = json.loads(read_file(controls / "login-status.json"))
        inventory = json.loads(read_file(controls / "inventory.json"))
        admission = json.loads(read_file(controls / "admission-record.json"))
        config = json.loads(read_file(controls / "server.json"))
        reply = json.loads(read_file(controls / "reply-audit.json"))
        observed = inventory.get("observed")
        if (
            status != {"loggedIn": True, "authMethod": "claude.ai"}
            or not isinstance(observed, list)
            or len(observed) != 1
            or not valid_inventory(observed[0])
            or not valid_nonce(config.get("nonce"))
            or admission != {"tools": observed[0], "nonce": config["nonce"]}
            or (controls / "admission.json").exists()
            or inventory.get("admitted") is not True
            or inventory.get("revoked") is not True
            or inventory.get("stop_reason") is not None
            or reply.get("failure") is not None
            or reply.get("exception") is not None
            or type(reply.get("require_harness_write")) is not bool
            or reply.get("text_returning") is not (not reply["require_harness_write"])
            or type(reply.get("harness_writes")) is not int
            or reply["harness_writes"] < int(reply["require_harness_write"])
        ):
            raise ValueError("accepted login-tools audit has failed admission or reply")
        read_calls(controls)
    return digest(raw)


def run_login_tools(
    work, spec, mission, system, wall, *, expected_identity=None, require_harness_write=False
):
    if os.environ.get("HARNESS2_MOJO_LIVE_APPROVED") != "1":
        raise ValueError("login-tools live session not approved")
    identity = runtime_identity()
    if expected_identity != identity:
        raise ImproverIdentityError("login-tools runtime differs from pinned attempt")
    status = login_status(runtime()[0], work)
    controls, command = prepare(work, spec, mission, system, identity)
    exclusive(controls / "login-status.json", encoded(status))
    audit = {
        "require_harness_write": require_harness_write,
        "harness_writes": None,
        "text_returning": not require_harness_write,
        "failure": "client_not_completed",
        "exception": None,
    }
    try:
        nonce = json.loads(read_file(controls / "server.json"))["nonce"]
        result = run_client(command, work, controls / "mission.txt", nonce=nonce, timeout=wall)
        audit["failure"] = result.reason
        try:
            check_stream(result.stdout)
        except TerminalStop:
            audit["failure"] = "end_conversation"
        except (ValueError, TypeError):
            audit["failure"] = audit["failure"] or "inventory_or_tool_policy"
        # The server must have started and closed cleanly even for a text-only
        # selector: otherwise missing MCP can silently degrade into guesses.
        calls = read_calls(controls)
        if runtime_identity() != identity:
            raise ImproverIdentityError("login-tools runtime changed")
        reply = captured_reply(result)
        writes = [
            v
            for v in calls
            if v.get("event") == "result"
            and v.get("tool") == "write_file"
            and str(v.get("path", "")).startswith("workspace/harness/")
            and v.get("result", {}).get("isError") is False
        ]
        audit["harness_writes"] = len(writes)
        if require_harness_write and not writes:
            audit["failure"] = audit["failure"] or "no_harness_written"
        if audit["failure"]:
            reply.text, reply.error = "", "login-tools stage refused: " + audit["failure"]
        if not reply.ok:
            audit["failure"] = audit["failure"] or "client_failure"
        return reply
    except BaseException as exc:
        audit["exception"] = type(exc).__name__
        audit["failure"] = audit["failure"] or type(exc).__name__
        raise
    finally:
        try:
            exclusive(controls / "reply-audit.json", encoded(audit))
        finally:
            bind_session(controls)
