# Mojo improver: whole-process sandbox

2026-10-31, follow-up 5, after the items 1–4 review. **Review branch, no live
authorization and no change to run 2.** No real credential or external model
was used for this implementation. Legacy JobBench/LAB/WorkBuddy improver channels
keep their old host execution path; they are not covered by this boundary.

**Auth revision (2026-10-31): the existing-login reader is removed, including
its tests.** Neither isolated mode discovers or reads saved Linux login files.
There are exactly two explicit sandbox authentication modes, described below;
`off` disables this sandbox and retains the original trusted host behavior.
No real credential, live-provider request or live rollout tested this revision.

**Subscription live hold:** the installed native client's Bash tool can read
`CLAUDE_CODE_OAUTH_TOKEN` through the client's `/proc/<pid>/environ` and copy it
into retained scratch. It does not need to inherit the variable itself. A fake
credential canary demonstrated this. Pass-through does not change this boundary.
The code-level `SUBSCRIPTION_TOOLS_REVIEWED=False` refuses subscription before
credential loading or launch, even with the generic live flag. No environment
override exists. Implementing/testing the protocol is not a claim that arbitrary
model-written files cannot contain the token. Prime/Adam must decide that trust
boundary before any subscription activation; API-key injection avoids giving
the real credential to the sandbox.

## What runs where

With `HARNESS2_MOJO_IMPROVER_AUTH=api-key` or `subscription`, Mojo's proposer,
composer, selector and reflection use `ChannelSpec.sandbox = "mojo-claude"`.
With the default `off`, they remain the original trusted host processes.
An invalid selection or failed selected sandbox never falls back to host mode.
The actual isolated native Claude Code process, all its tools, hooks,
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
- CLI settings are immutable mounts; hooks and configured plugins are disabled,
  with an explicit empty MCP configuration. Subscription uses the isolation
  flags, not `--bare`; API-key mode uses `--bare`. Built-in client tools/plugins
  can still appear, depending on client version. Settings narrow behavior;
  the process namespace, not the client's tool list, is the security boundary.

The optional compiler/Pi mounts let the improver compile probes or test an
extension against a mock **inside** its namespace. They give it no route to the
real executor serve. Package downloads and direct WebFetch are blocked.

## Explicit authentication modes and API-only network

The namespace has no host networking. Its `127.0.0.1:8092` relay reaches one
private Unix socket. The host gateway creates verified TLS connections only to
`api.anthropic.com:443`; neither client URLs nor proxy environment can change
that destination. It accepts `POST /v1/messages` and `/v1/messages/count_tokens`,
with optional `?beta=true`, and answers the optional hello HEAD probe locally.
No CONNECT, redirects, token refresh, arbitrary paths or arbitrary models.

`HARNESS2_MOJO_IMPROVER_AUTH` is recorded in the benchmark and channel identity:

- `off` (default): original trusted, unsandboxed improver. This is an operator
  selection, never a fallback after a sandbox/authentication failure.
- `subscription`: Adam generates a token with `claude setup-token` and supplies
  `CLAUDE_CODE_OAUTH_TOKEN`. If the live hold is separately resolved, the framework
  passes it over a bounded anonymous stdin pipe into the sandbox entry, which
  puts it in the native client's environment, not systemd/bwrap argv, settings
  or run manifests. Framework transcripts redact literal echoes, not encoded
  copies. The broker has **no
  configured OAuth credential**: it forwards each client's Authorization value
  and OAuth `anthropic-beta` capability unchanged; no injection, refresh or login
  discovery. The real token is accessible to that client's sandboxed tools as
  explained above. It is a model-request token, not a Remote Control/connector
  credential. This mode must not use `--bare`.
- `api-key`: Adam explicitly provides `ANTHROPIC_API_KEY` to the host process.
  The sandbox gets only an inert placeholder under that variable. The broker
  replaces the placeholder `x-api-key` with the host-held key. The client uses
  `--bare`. This is billed per token to the key's account, not the subscription.

Selected credentials must be nonempty bounded ASCII values. Missing/malformed
ones refuse without printing them. Other inherited credentials and provider/
proxy overrides never enter the namespace. The bridge forwards the original
body and version/beta/user-agent headers but not cookies or client-selected Host.
Mixed or duplicate authentication refuses. Subscription requires an OAuth beta
capability; API-key mode requires the exact placeholder, not a supplied real key.

The native client runs with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. WebFetch
preflight and fast-mode side requests cannot bypass the network namespace;
there is no SOCKS route. These fakes test protocol wiring, not real-account
acceptance or permission for live use. See [authentication/setup-token and bare
mode](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token),
[subscriptions and gateways](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways),
and [gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol).

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
downloads, account-management and unsupported side requests cannot. Real credential,
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
before launch, after execution and before acceptance; detected drift
stops the call without another model attempt. Mojo's run identity now names the
new policy, so historical run namespaces cannot silently resume under it.

The audit carries the per-attempt runtime identity, model-call count, last broker
diagnostic, process result, seconds and confirmed teardown. This is selected-file
identity, not a complete hermetic toolchain or whole-run immutability proof.
Operator-owned toolchains, source, context and audit paths must remain quiescent;
this is not protection against a hostile host writer or kernel vulnerability.

## Retained attempt scratch and transcripts

An isolated proposer requires an explicit durable `audit_dir`. Every attempt,
including failed attempts and resumed calls, reserves a new owner-private
`_attempts/attempt-*/` directory. Only its `work/` child is mounted read-write in
the namespace. The host's mission, system text, started record, stdout/stderr
and finished record are siblings outside that mount (directories 0700, new
records 0600). They are not model-editable through the namespace.

The native client requests `stream-json --verbose`, preserving message/tool
events rather than only the final reply. A usable reply requires a terminal
typed result event, successful process status and no resource-limit reason.
The bounded UTF-8-decoded stdout and stderr are saved after literal credential
redaction; an echo fails the attempt rather than yielding a usable reply.
This does not remove encoded credentials or cleanse arbitrary scratch files.
Invalid/partial streams are retained but never adopted. The finished
record binds all host-recorded files; returned/cached replies reference its
digest, and cache reuse verifies it and its file hashes.

The process supervisor's existing 4 MiB output limit still applies. These are
bounded captured streams, **not a claim that every tool event survived a crash**.
If launch failed, teardown is unknown, or output is unavailable, the record says
so; it does not fabricate an empty successful transcript. A hard host kill can
leave only `started.json`. Audit-write failures stop the call without a retry
or success cache. They leave the owned attempt for reconciliation.

Scratch is retained **in place**, including private HOME, tmp and caches, on
success and failure. It is mutable, untrusted, and not hash-bound; it may contain
symlinks or special files. The audit reader never traverses it. Never open it
with a link-following bulk reader or treat it as the validated harness; only the
separate bounded/no-follow harvest can be reused. Unknown teardown remains a
fatal stop with no scratch inspection or harvest. Confirm the service has stopped
before manually inspecting or removing an incomplete attempt.

Retention is deliberate, not an automatic `/tmp` leak: there is no auto-purge.
Budget disk space across attempts. The per-attempt scratch watchdog (256 MiB /
8,192 entries) is still a watchdog, not a hard quota or whole-run disk limit.
Old `9c23168` runs cannot retroactively acquire transcripts or deleted scratch.

## Checks and remaining live gate

`test_improver_sandbox.py` covers isolation arguments, mocked whole-launch
authentication, collection/cache attacks, runtime drift and all Mojo channels.
`test_improver_broker.py` uses fake credentials and fake upstream connections for
route/model/budget, credential handling, streamed responses and closing races.
`test_improver_namespace.py` is the separate tiny real namespace suite: private
paths/environment/devices, network isolation, immutable controls, fake relay,
output/timeout/descendant teardown, and native Claude calling its Bash tool
against fake APIs in both modes (flags vs bare). It neither compiles nor invokes
Pi or any real model. `test_improver_auth_modes.py` covers explicit selection,
env-only credential sources, pipe delivery, literal redaction and the live hold.
`test_improver_retention.py` covers the live hold, durable scratch, success,
failure/retry/resume, missing or changed audit files, unknown teardown, refused
partial streams and audit-write failure. The real native-client fake test also
requires both a Bash `tool_use` and its `tool_result` in the retained stream.

Before any live run: Prime's six pending namespace/compiler/Pi controls for
items 1–4 still stand; review this delta, resolve the tool-token hold if choosing
subscription, and separately obtain Adam's approval for a bounded credential/model
check. Do not probe :8091, read saved login files, refresh
credentials, install anything, or widen the gateway merely because a fake test
passed. A rejected live request comes back with its status/diagnostic; no silent
switch to the old host route.
