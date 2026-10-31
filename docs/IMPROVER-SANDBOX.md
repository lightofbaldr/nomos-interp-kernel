# Mojo improver: whole-process sandbox

2026-10-31, follow-up 5, after the items 1–4 review. **Review branch, no live
authorization and no change to run 2.** No real credential or external model
was used for this implementation. Legacy JobBench/LAB/WorkBuddy improver channels
keep their old host execution path; they are not covered by this boundary.

## What runs where

Mojo's proposer, composer, selector and reflection use `ChannelSpec.sandbox =
"mojo-claude"`. The actual native Claude Code process, all its tools, hooks,
extensions and descendants run inside bubblewrap. This is separate from
Claude Code's built-in shell sandbox, whose documented scope is shell commands
and their children, not every tool. [Claude sandbox scope](https://code.claude.com/docs/en/sandboxing).

- Fresh private PID/network namespace; capabilities dropped; further user
  namespaces disabled. `/usr` is read-only. Only the explicit Claude binary,
  Mojo compiler prefix and Node/Pi toolchain are added, also read-only.
- `/work` is the private scratch copy. HOME, tmp and caches are inside it. Host
  home, login files, user bus, GPU devices, real task sources, run roots and
  grader/reference files are not mounted. Context includes only the method's
  already-selected evidence and editable harness.
- The existing systemd service limits apply: 4 GiB memory, zero swap, 128 tasks,
  200% CPU quota, 64 MiB per file, 256 descriptors, at most 3,600 seconds and
  bounded stdout/stderr. The 256 MiB / 8,192-entry scratch watchdog is not a
  hard disk quota. The improver additionally verifies that the service is
  inactive/failed with no remaining control group before collection.
- Unknown teardown is fatal: no harvest or retry. Its scratch path is retained
  and reported for operator reconciliation. Do not delete/reuse it until the
  service is independently confirmed stopped. No automatic host-health action.
- CLI settings are immutable mounts; other settings sources, plugins, hooks,
  MCP auto-loading and slash commands are disabled. These settings narrow
  behavior; the process namespace is the security boundary.

The optional compiler/Pi mounts let the improver compile probes or test an
extension against a mock **inside** its namespace. They give it no route to the
real executor serve. Package downloads and direct WebFetch are blocked.

## Authentication and API-only network

The namespace has no host networking. Its `127.0.0.1:8092` relay reaches one
private Unix socket. The host gateway creates verified TLS connections only to
`api.anthropic.com:443`; neither client URLs nor proxy environment can change
that destination. It accepts `POST /v1/messages` and `/v1/messages/count_tokens`,
with optional `?beta=true`, and answers the optional hello HEAD probe locally.
No CONNECT, redirects, token refresh, arbitrary paths or arbitrary models.

The actual Claude Code client uses `CLAUDE_CODE_OAUTH_TOKEN` with an inert
placeholder. On the **host only**, after `HARNESS2_MOJO_LIVE_APPROVED=1`, the
gateway loads the private Linux login file `~/.claude/.credentials.json`. It
requires owner-only permissions, a regular single-link file, inference scope
and expiry covering the attempt plus 30 seconds. It uses only the access token,
never refreshes it, and never copies the file or refresh token into the sandbox.
Missing/expired login refuses; refresh outside the run under operator control.
There is no API-key, Vertex, Bedrock, Codex or host-execution fallback.

The supported integration uses the actual CLI and preserves its Messages body,
`anthropic-version`, `anthropic-beta` and user-agent headers. Client authorization
is replaced by the real bearer credential in host memory. Client cookies,
API keys, Host and proxy headers are not forwarded. This follows the documented
gateway routes and OAuth-header requirements; the fake API test does **not**
establish that a real account accepts them. [Gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol),
[OAuth environment setting](https://code.claude.com/docs/en/env-vars).

Model aliases permit only their own family; explicit Claude model IDs require
an exact match. Aliases use the client's own resolution; explicit IDs also pin
the auxiliary default model settings. A different auxiliary model is refused,
not silently allowed. Limits per process attempt: 128 admitted API calls
(including count-tokens and unsuccessful requests), 4 MiB request, 32 MiB
response, 32,768 output tokens per request, and four local connections with
serialized upstream requests. The CLI turn bound is 120 by default. Existing
Proposer retry policy remains separate; these are not a run-wide paid-call cap.

HTTP failures keep status but use fixed diagnostics, not upstream prose, headers
or exception text. SSE is streamed; literal credential echoes are refused even
when split across chunks. A broken or over-budget stream closes without becoming
a valid response. Closing an attempt revokes queued admission and active sockets.

API-backed web search can remain usable through this endpoint; direct browsing,
downloads, account-management and unsupported side requests cannot. Real login,
model-family resolution and docs-search behavior remain **unverified live**.
The namespace, not the traffic-disable environment setting, enforces isolation.

## Collection, cache and identity

The host snapshots context once, then hashes and copies those same bytes. All
input/collected trees must contain regular single-link files: no symlinks,
hardlinks, sockets, FIFOs, escaping subpaths or linked parents. Reads are
descriptor-relative and bounded at 32 MiB, 4,096 entries and depth 32. Output
collection happens only after process teardown. Context/run originals remain
untouched. The collection hardening also applies to legacy channels, without
claiming their processes are sandboxed.

Sandboxed cache keys bind the selected runtime hashes, policy, channel/model,
effective timeout, collection path and context snapshot. Host-mode cache entries
cannot be reused. Audit metadata additionally binds response and harvested-tree
digests; substitution refuses, including linked cached files. Runtime is checked
before authentication, after execution and before acceptance; detected drift
stops the call without another model attempt. Mojo's run identity now names the
new policy, so historical run namespaces cannot silently resume under it.

The audit carries the per-attempt runtime identity, model-call count, last broker
diagnostic, process result, seconds and confirmed teardown. This is selected-file
identity, not a complete hermetic toolchain or whole-run immutability proof.
Operator-owned toolchains, source, context and audit paths must remain quiescent;
this is not protection against a hostile host writer or kernel vulnerability.

## Checks and remaining live gate

`test_improver_sandbox.py` covers isolation arguments, mocked whole-launch
authentication, collection/cache attacks, runtime drift and all Mojo channels.
`test_improver_broker.py` uses fake credentials and fake upstream connections for
route/model/budget, credential handling, streamed responses and closing races.
`test_improver_namespace.py` is the separate tiny real namespace suite: private
paths/environment/devices, network isolation, immutable controls, fake relay,
output/timeout/descendant teardown, and native Claude calling its Bash tool
against a fake API. It neither compiles nor invokes Pi or any real model.

Before any live run: Prime's six pending namespace/compiler/Pi controls for
items 1–4 still stand; review this delta and separately obtain Adam's approval
for a bounded real-login/model check. Do not probe :8091, read the login, refresh
credentials, install anything, or widen the gateway merely because a fake test
passed. A rejected live request comes back with its status/diagnostic; no silent
switch to the old host route.
