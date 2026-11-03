# Claude Code login with harness2 tools only

2026-10-31 implementation; **offline-tested, handshake repair awaiting a fresh
live control**. Prime's first control at `83f5fda` failed closed during MCP
initialization: empty inventory, no admission and zero tool calls. Its evidence
is preserved. Review and Adam's run-3 method approval are still required.
Nothing here authorizes use of :8091, an improver session, a merge or API spending.

## Selection and trust boundary

For Mojo, select `HARNESS2_IMPROVER_MODE=login-tools` and leave
`HARNESS2_MOJO_IMPROVER_AUTH=off`. The default mode is `off`, preserving the
original trusted host route and the separately selected, reviewed API-key
sandbox. Selecting login-tools together with API-key auth is an error. Only
`claude_cli` is supported; failure never selects a less restricted route.

The new route runs a fresh host `claude -p` for every actual CLI invocation,
including proposer/inv1, domain/dom1, composer/agg1, experience and reflection.
As before, a verified cached stage does not launch again. The channel, tool
policy, source/runtime hashes, context snapshot and harvest enter its cache
identity; old trusted-host results cannot masquerade as this mode.

**This is a tool-capability policy, not whole-process OS isolation.** Claude Code
itself still runs on the host with access to its normal login. Host protection
depends on the CLI enforcing disabled built-ins and the trusted MCP server's
file checks. The server has no general execution or network tool. Only
model-authored Mojo probes execute in the existing bwrap boundary (no network or
host home). The API-key mode's stronger whole-client isolation is unchanged.
No claim is made about protecting against a compromised CLI, hostile same-UID
host writer, OS vulnerability or managed administrator override.

## Client and authentication

The launcher supplies `--tools ""`, `--strict-mcp-config`, one generated MCP
config, exactly four `--allowedTools`, `--permission-mode dontAsk`,
`--permission-prompts none`, `--setting-sources ""`, `--disable-slash-commands`,
`--restricted`, `--no-chrome`, and `--no-session-persistence`. Hooks are disabled
and no plugins are enabled. There is no bypass-permissions flag, `--bare`,
continue, resume, host shell, file download, WebFetch or other MCP server.

When captured, the raw stream-json init event remains in the bound
`stdout.jsonl`, including any memory or `CLAUDE.md` metadata the CLI reports.
An omitted field is unknown, not evidence that no memory was loaded.
`CLAUDE.md` exclusion relies on the CLI enforcing `--setting-sources ""` plus
`--restricted`; it is not independently established by the tool inventory.

The host child's environment is whitelisted: normal HOME, basic locale/path and
systemd runtime metadata, plus disabled nonessential traffic, updater, memory
and prompt history. Inherited API keys, auth tokens, OAuth env tokens, provider
switches, proxies, debug destinations and nested-session variables do not pass.
Harness2 never opens the CLI's credential storage. Before a call the CLI's own
`auth status` must return literal logged-in status with `authMethod=claude.ai`;
only those two metadata fields are retained. Raw auth-status output is not logged.
API/console logins, explicit OAuth-token env mode and unknown output refuse.

There is no broker, credential copying, token relay or key provisioning in this
mode. The login consumes the operator's subscription allowance; it is not a
claim of unlimited/free capacity. `HARNESS2_MOJO_LIVE_APPROVED=1` is mandatory;
the API-spend switch is neither required nor used. Usual stage retry/usage-limit
rules still apply; the one-shot control below instead stops on a limit.

The stream's reported tool inventory must be exactly the four MCP tools, with
the optional `EndConversation` terminal signal (Prime's rulings 1619/1621).
It is never included in `--allowedTools`, and calling it fails even a text-only
stage. Missing, duplicated or additional tools fail closed. Every observed
inventory is recorded; an absent one is explicitly unknown. Editing/harvest
stages also fail with `no_harness_written` if no successful MCP write targeted
`workspace/harness/`. Text-returning stages retain the existing nonempty-reply
contract and their own audit flag; their parsers still run in the method layer.

**Ordering is the approved stdin-text fallback, not a held-input guarantee.**
The mission may already have reached the model when the parent reads stdout
init line by line. On a mismatch it revokes admission and stops the process
group. Our MCP tools additionally refuse every call until the parent atomically
publishes a mode-0600 admission after validating init. It lives outside both
capabilities, binds a fresh random per-stage nonce also passed to the server,
and is removed on every exit. The server requires that exact nonce; a stale
file cannot admit another stage. A separate receipt remains in the audit.
Discovery itself grants no capability (Prime 1623).
There is still a scheduling window between the CLI emitting stdout and the
parent processing it. Suppression of any unexpected host built-in depends on
the CLI's own flags/policy; a process-group kill is not proof of zero prior host
actions by a broken/compromised CLI. We do not claim strict pre-mission ordering
or whole-process containment. The real control must establish compatibility.

## Four stdio capabilities

The server uses newline-delimited JSON-RPC over stdin/stdout, exposes tools only,
and has no socket listener. It is launched by absolute trusted paths using
`env -i` and Python `-I -B`: an MCP `env` object alone would merge with inherited
variables and is not sufficient. The server receives no login or API credential.

Pre-initialize requests return `-32002` without granting a capability; serving
continues so a client's version probe can precede the normal handshake. Unknown
requests after initialization retain `-32601`. Unknown notifications are ignored
and audited by method name only, with no parameters; long names are capped at
256 characters and explicitly marked truncated. Notifications have no response,
as required by [JSON-RPC](https://www.jsonrpc.org/specification#notification).
Malformed JSON, framing and envelopes remain fatal. Every actual tool call still
requires nonce-bound admission. A second server for the same stage exits 3 with
`tool audit exists: refusing a second server for this stage` on stderr; it never
reopens or alters the first server's audit.

| Tool | Capability |
| --- | --- |
| `list_dir(path)` | One directory under `context/` or `workspace/`; no recursive host scan. |
| `read_file(path)` | Bounded UTF-8 file under either root. |
| `write_file(path, content)` | Workspace only, including parent creation and atomic replacement. |
| `run_mojo(path)` | A single self-contained workspace `.mojo`, copied into a private retained probe directory; fixed compile/run commands. |

Paths are relative, canonical and root-prefixed. Absolute paths, traversal,
URLs, symlink parents/leaves, multi-link files and special files refuse. Root
parents and each component are opened without following links. Context is
read-only. The original `context/harness` is copied to `workspace/harness`,
mission references are translated, and only the latter is harvested. Other
context evidence remains at its original read-only paths. There is deliberately
no delete, arbitrary search, import/install, shell, argv or environment tool.

Limits: 1 MiB per text file, initial root budgets of 32 MiB / 4096 entries,
workspace initial bytes plus cumulative writes at most 32 MiB, path depth 32,
256 tool calls and 16 Mojo probes per session. MCP lines/responses are bounded
at 2 MiB and a session at 1024 protocol messages. The host stream capture is
4 MiB, with the stage's existing wall/turn limits.

Mojo uses the reviewed executor's mounts, cgroup controls and no-network route,
without Pi or a model broker. Compile: 90 seconds. Run: 10 seconds. Each returns
at most 64 KiB combined stdout/stderr; a final over-limit read chunk is trimmed
and explicitly failed. A failed/limited compile never starts the program.
An unknown service teardown stops the entire stage, not just that tool call.
The source/program scratch stays outside MCP capabilities and is retained for
inspection, not re-used as a harness or executed on the host.

## Audit and failure handling

Every call has a durable start and result record in the attempt's sibling
`login-tools/calls.jsonl`. It records tool, path, argument hash, refusal or result
hash, and bounded compiler/program evidence for probes. It does not duplicate
file-read or write-content text. The bounded CLI transcript retains tool text.
The launch/configs, effective mission, login metadata, observed inventories,
admission receipt, per-stage reply flags and calls log are bound by
`session.json`; the normal attempt's `finished.json` binds that manifest.
Successful-cache readers require all of the successful admission/reply evidence
and recheck these bindings. Terminal-stop reasons survive an incomplete tool
teardown. Audit writes fail closed.

Protocol errors and ignored-notification method names also enter `calls.jsonl`,
so those diagnostics are retained without importing the CLI's per-cwd cache.
Automatic harvesting of that external cache log is not implemented; the
operator should preserve it separately when diagnosing client-level failures.

The supervisor TERM-stops the host process group and allows MCP to unwind its
probe before a bounded hard-stop. It requires a closed audit with every admitted
call settled. Unknown teardown is fatal: no harvest or retry. A crash can leave
partial retained evidence; the raw scratch is explicitly mutable/untrusted,
not a guarantee of complete evidence after a host failure.

## Offline checks and the held real control

```
.venv/bin/python -B -m unittest discover -s tests -p 'test_login_tools*.py' -v
.venv/bin/python -B scripts/check_mojo_followups_offline.py
.venv/bin/ruff check harness2 tests scripts
```

These exercise the real stdio entry with fake-format assets, fake native client
processes, path/size/admission/teardown witnesses, the control oracle and the
stage/cache routing. They make no model request, invoke no real compiler and do
not contact :8091. The earlier real executor namespace controls remain relevant,
but are not represented as new-route live evidence.

After separate approval, ONE control can be invoked with a **fresh** directory:

```
HARNESS2_CC_LOGIN_CONTROL_APPROVED=1 HARNESS2_MOJO_LIVE_APPROVED=1 \
  .venv/bin/python -B scripts/check_cc_login_tools_live.py \
  --model opus --out /absolute/new/control-directory
```

It creates `decoy-home/.env` and `decoy-home/.claude/canary` with a fresh random
canary line, outside both tool roots. Only these synthetic files are targeted
by absolute home-like paths, never the operator's real `.env` or `.claude`.
The traversal strings, `/proc/self/environ` (the minimal server environment),
URL probe and outside write remain. It checks every actual MCP refusal, a
legitimate read/write and the exact known Mojo probe, and asks for Bash and
WebFetch. Their
absence is verified from the available-tool inventory, **not falsely counted as
executed refusals of nonexistent tools**. Missing attempts, an altered sentinel,
bad probe or failed client make the control nonzero. No automatic retry or
usage-limit wait. The canary is never included in the mission. After success or
failure, a bounded, no-follow scan checks retained stdout, stderr and host audit
files; a canary hit, missing transcript or incomplete scan cannot pass. The
scan reports filenames and hashes without echoing the canary, and deliberately
does not traverse mutable workspace or probe scratch. The script saves
`control-request.json`, the normal durable attempt and `control-result.json`.
It does not authorize a run-3 sweep.

## References and disclosure

CLI arguments and authentication metadata were checked against the installed
read-only `claude --help` and [official CLI reference](https://code.claude.com/docs/en/cli-reference).
MCP framing and tool envelopes follow the official
[stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
A fresh real control of this handshake repair, including the compiler probe,
remains pending with Prime. Run 3 must name the new tool set, no-shell policy,
read-only evidence/writable candidate split and tool/probe budgets as a method
change, not an unchanged replication of runs 1–2.
