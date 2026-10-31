# Undici — exposure, runtime pin and dependency repair

## Current source policy

Glimmer Lab supports exactly **Node 24.22.1 / bundled Undici 7.30.1**. The runtime
was already installed; no runtime installation or change to nvm's global/default
selection was needed. `.nvmrc`, the npm engine requirement and checks in the
build, launcher and server hook pin this combination. Startup records the actual
executable and versions before the adapter import. Wrong or missing identities
refuse. The preview refresher checks before touching tmux and launches that same
verified executable through `start.mjs`.

The **separate npm copy** is overridden and locked to **6.29.1**. Both the lock
and installed copies are checked by `scripts/check-dependencies.mjs`. Neither the
npm override nor changing a shell's default upgrades an already-running Node
process. The operator must use the reviewed launcher/runtime when next deploying;
this change did not restart the live service.

All five cited upstream advisories list 6.29.1 and 7.30.1 as patched releases:

| Advisory | Relevant behavior |
| --- | --- |
| [GHSA-xjj6-f33q-929c](https://github.com/nodejs/undici/security/advisories/GHSA-xjj6-f33q-929c) | Excessive content encodings followed by an early close can crash affected fetch clients. |
| [GHSA-2m5v-pch6-74hx](https://github.com/nodejs/undici/security/advisories/GHSA-2m5v-pch6-74hx) | Agent loses function-valued TLS certificate-check options. |
| [GHSA-qrxm-fqhp-jwvm](https://github.com/nodejs/undici/security/advisories/GHSA-qrxm-fqhp-jwvm) | Certificate-changing TLS 1.2 renegotiation can crash affected clients. |
| [GHSA-4qv8-2p9j-2685](https://github.com/nodejs/undici/security/advisories/GHSA-4qv8-2p9j-2685) | EventSource parser state can grow without bounds. |
| [GHSA-9fm3-97px-rjf6](https://github.com/nodejs/undici/security/advisories/GHSA-9fm3-97px-rjf6) | WebSocket automatic PONG buffering can grow without bounds. |

## Exposure and its limit

In the demo configuration, server fetch reaches the **operator-fixed loopback
Nomos endpoint** and the fixed Meta TLS endpoint
`https://api.meta.ai/v1/chat/completions`. The browser and model cannot supply an
upstream URL. Meta requests reject redirects. Pi uses its stock OpenAI-compatible
transport; there is no claim of a new custom redirect/transport policy here.

This is a **deployment exposure statement**, not a loopback-only upstream
allowlist: `checkedBase` permits an operator-configured remote HTTP(S) Nomos
origin. Moving Nomos to another host changes that trust boundary and needs the
corresponding deployment review. The UI's loopback Host guard controls access to
the local UI; it is not a network firewall for outgoing requests.

`meta-client.ts`, `pi-service.ts`, the legacy `nomos.ts` proxy and Pi's stock
OpenAI client use server fetch. No server-side EventSource/WebSocket constructor,
function-valued TLS configuration or custom Undici Agent was found in current app
source. Browser SSE is separate. The npm copy is also available to AI SDK provider
utils' download helper; no application call of that helper was found. These are
static reachability observations, not proofs for every dependency path.

Fixed trusted peers reduce exposure but did not make the old runtime's fetch DoS
unreachable. Patching both copies removes the five listed vulnerable-version
matches; it is not a blanket security certification or an exploit test.

## Historical record

At slice C `71c00e0`, this Codex shell selected Node **24.18.0**, with bundled
Undici **7.28.0**; the npm copy was **6.29.0**. Those versions fall within the five
advisories. Prime then confirmed that his A/B reviews used the already-installed
Node 24.22.1, not this older shell runtime. Both observations remain true; the
earlier test records have not been rewritten. Prime was notified initially in
comms 1546, and authorized this follow-up.

The change is limited to runtime selection/validation, the npm override, tests and
documentation. No vulnerability was exercised against the serve or Meta; no paid
call, live model request, runtime installation or service restart was performed.

## Offline verification

On the supported installed runtime:

- Runtime/dependency controls: 5/5. An old/missing/mismatched identity refuses;
  a stale PATH cannot displace the installed pinned Node; an explicit bad
  executable does not fall back. The real installed Node 24.18.0 was also refused
  before the adapter import.
- Startup: real `npm start` binds only loopback by default (socket table and
  HTTP checked), logs Node 24.22.1 / Undici 7.30.1 and the actual executable.
  Explicit wide binds are checked without opening their sockets. A direct raw
  adapter start with the wrong bundled version refuses before any listen attempt.
- Full app: fresh check/build, live/annotator/profile/file/Mojo integration and
  browser regressions pass. Mojo sandbox 7/7 and integration 4/4; file sandbox
  15/15 and integration 6/6. All model providers are fixtures; no live upstream.
- Unchanged agent `a84c710`: 65/65, typecheck/build, vendor and package checks on
  Node 24.22.1. Its source and the app's vendored archive are unchanged.
- `npm ls undici --all` resolves one deduplicated npm copy at 6.29.1.
  `npm audit --json` no longer lists Undici, but reports **19 low-severity**
  findings elsewhere (zero moderate/high/critical). No automatic audit fixes.

Reproduce the lab checks with `node scripts/runtime-regression.mjs` after
selecting the supported runtime. The final full pass also used `CLAUDECODE=1`,
including fresh check/build and the raw-adapter refusal. The local gate ledger is
`.unlazy/node-undici-c/GATES.md`; full runner output is retained in
`/tmp/glimmer-runtime-regression-final.log`, agent output in
`/tmp/glimmer-runtime-agent-final.log`. These checks do not authorize a live E2E,
deployment restart or merge. The gate workflow keeps those out of scope.

Retention follow-up: the accepted logs (plus the refusal witness, audit, early
run records and gate ledgers) are copied byte-for-byte to the ignored
`.unlazy/workbench-c-live/prior-evidence/`, with `SHA256SUMS`. The `/tmp` names
above remain the historical run locations; the retained copies survive a reboot.
