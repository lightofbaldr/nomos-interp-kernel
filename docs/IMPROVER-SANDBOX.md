# Mojo improver: whole-process sandbox

This document describes the unchanged API-key route. The separately selected
[login-tools mode](CC-SESSION-IMPROVER.md) keeps CC on the host and is **not** this
whole-process boundary. Neither it nor this route reads or relays stored login
credentials through a broker.

2026-10-31, follow-up 5, after the items 1–4 review. **Review branch, no live
authorization and no change to run 2.** No real credential or external model
was used for this implementation. Legacy JobBench/LAB/WorkBuddy improver channels
keep their old host execution path; they are not covered by this boundary.

**API-key-only revision (2026-10-31):** the saved-login reader, subscription
pass-through, token-delivery pipe and their tests are deleted. Subscription mode
is rejected, not an offered route with a documentation caveat. See the
[decision and archived `/proc` witness](IMPROVER-AUTH-DECISION.md). The one
isolated route is host-held API-key injection; `off` retains the original trusted
host behavior. No real credential, live-provider request or live rollout tested
this revision. API mode remains held until Adam supplies a key and explicitly
approves per-token spending.

## What runs where

With `HARNESS2_MOJO_IMPROVER_AUTH=api-key`, Mojo's proposer,
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
  with an explicit empty MCP configuration. The client uses `--bare`.
  Built-in client tools/plugins
  can still appear, depending on client version. Settings narrow behavior;
  the process namespace, not the client's tool list, is the security boundary.

The optional compiler/Pi mounts let the improver compile probes or test an
extension against a mock **inside** its namespace. They give it no route to the
real executor serve. Package downloads and direct WebFetch are blocked.

## Explicit admission and API-only network

The namespace has no host networking. Its `127.0.0.1:8092` relay reaches one
private Unix socket. The host gateway creates verified TLS connections only to
`api.anthropic.com:443`; neither client URLs nor proxy environment can change
that destination. It accepts `POST /v1/messages` and `/v1/messages/count_tokens`,
with optional `?beta=true`, and answers the optional hello HEAD probe locally.
No CONNECT, redirects, token refresh, arbitrary paths or arbitrary models.

`HARNESS2_MOJO_IMPROVER_AUTH` is recorded in the benchmark and channel identity:

- `off` (default): original trusted, unsandboxed improver. This is an operator
  selection, never a fallback after a sandbox/authentication failure.
- `api-key`: Adam provisions a private key file and explicitly points
  `HARNESS2_IMPROVER_API_KEY_FILE` at its absolute path. The host never reads the
  inherited `ANTHROPIC_API_KEY`: a parent Claude Code session can set that to its
  own internal credential. Missing dedicated configuration refuses even if the
  inherited variable is present. The sandbox gets only an inert placeholder
  under `ANTHROPIC_API_KEY`. The broker
  replaces the placeholder `x-api-key` with the host-held key. The client uses
  `--bare`. This is billed per token to the key's account, not the subscription.

Selecting `api-key` alone is insufficient. Launch requires both
`HARNESS2_MOJO_LIVE_APPROVED=1` and the separate
`HARNESS2_MOJO_IMPROVER_API_APPROVED=1`, plus the dedicated key file. The separate
API approval records Adam's explicit spending decision, not the executor's existing live approval.
Without it, admission refuses before reading the key or starting the sandbox.
The `.env.example` defaults are `off`, API approval `0`, and an empty file path;
this implementation does not set either flag or provision a key. A missing or
malformed key after approval also refuses before launching anything. There is no fallback to host
execution after an isolated admission or authentication failure.

The file must be a regular file owned by the current user, with mode exactly
`0600` and a single hard link. The loader opens every path component without
following symlinks, bounds the read, and checks metadata before and after.
Its contents are one 16..4096-byte printable ASCII key, optionally ending with
one LF or CRLF; additional lines or whitespace refuse. Errors never include the
key or file contents. Adam should provision it outside all sandbox-mounted paths
and run roots (for example in his private host configuration directory); the
operator-owned source must stay quiescent. It is read once into host memory and
closed before launch, never mounted, copied or put in the client environment or
run identity. These checks are not a defense against a hostile host writer.

Missing/malformed values refuse without printing them. Other inherited
credentials and provider/proxy overrides never enter the namespace. The bridge forwards the original
body and version/beta/user-agent headers but not cookies or client-selected Host.
Any Authorization header, duplicate authentication, or key other than the exact
placeholder refuses. The broker always injects its own host-held key. There is
no client-credential forwarding branch and no saved-login discovery.

The native client runs with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. WebFetch
preflight and fast-mode side requests cannot bypass the network namespace;
there is no SOCKS route. These fakes test protocol wiring, not real-account
acceptance or permission for live use. This is the API-key gateway design, not a
subscription-login adapter.

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
against a fake API-key gateway, with `--bare`. It neither compiles nor invokes
Pi or any real model. `test_improver_api_key.py` covers B-only selection,
dedicated-file admission, placeholder delivery, literal redaction, and independent
API-spend admission. It also rejects the removed subscription selection.
`test_improver_key_file.py` covers inherited-key refusal before broker/spawn,
file-vs-parent key selection, the sole environment lookup, permissions, owner,
links, special files, size/encoding and a change during the read.
`test_improver_retention.py` covers the live hold, durable scratch, success,
failure/retry/resume, missing or changed audit files, unknown teardown, refused
partial streams and audit-write failure. The real native-client fake test also
requires both a Bash `tool_use` and its `tool_result` in the retained stream.

Before any live run: Prime's six pending namespace/compiler/Pi controls for
items 1–4 still stand. The revised namespace/native-fake controls are also deferred
until run 2's heldout phase is finished; this revision runs only the CPU-light
offline suite. Review this delta, then separately obtain Adam's key and explicit
API-spend approval for a bounded model check. Do not probe :8091, read saved login files, refresh
credentials, install anything, or widen the gateway merely because a fake test
passed. A rejected live request comes back with its status/diagnostic; no silent
switch to the old host route.
