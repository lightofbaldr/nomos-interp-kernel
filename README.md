# glimmer-agent

The Glimmer agent's Pi harness: the `nomos` provider (readout requests to the Nomos serve), the pi-durable readout
extension (settings as rewindable documents, records in the transcript and never in the model's context), and the
bridge that streams a conversation to the UI and the reports.

Design: glimmer-emotions `docs/superpowers/specs/2026-10-20-readout-spine-design.md`, section 4.

## Development

Validated on Node 24.18.0. Pi AI, Pi Durable and Chord are pinned to 1.3.0; `src/pi.ts` is the only Pi Durable import
boundary. The launcher witnesses also need Python 3 (standard library only). CPU tests use local fake HTTP servers,
real Pi APIs and memory/SQLite storage, not a GPU.

```sh
npm ci
npm run verify:vendor
npm test
npm run typecheck
```

The vendor check compares the installed package.json files to the audited tarballs; the lockfile additionally pins
package tarball integrity. See [the implementation ledger](docs/implementation-log.md) for evidence and deviations.

## Control-plane integration

These are server-side modules, not browser components or a deployed HTTP gateway. Keep the harness alive while UI
clients connect and disconnect. Install the extension once, register the provider with that host's request map, open
SQLite, and attach the host:

```ts
import { createModels } from "@earendil-works/pi-ai";
import { BACKGROUND_CONTEXT as ctx, createRegistry, Harness, openNodeSqliteStorage } from "./src/pi.ts";
import { ReadoutHost, readoutExtension } from "./src/extension.ts";
import { NOMOS_MODEL, NOMOS_PROVIDER, nomosProvider } from "./src/provider.ts";
import { openBridge } from "./src/bridge.ts";

const host = new ReadoutHost(ctx);
const models = createModels();
models.setProvider(nomosProvider({ baseUrl: "http://127.0.0.1:8091/v1", requests: host.requests }));
const registry = createRegistry();
registry.install(readoutExtension(host));
const harness = await Harness.open(await openNodeSqliteStorage("sessions.sqlite"), { models, registry }, ctx);
host.attach(harness);

// New chat: create a new independent conversation; root() returns the same reserved root on later calls.
const chat = await harness.createConversation({
  ownership: { kind: "ownerless" },
  agent: { model: { provider: NOMOS_PROVIDER, modelId: NOMOS_MODEL } },
}, ctx);

// Your authenticated transport supplies the send callback; await it for backpressure.
const bridge = await openBridge(harness, host, chat.id, ctx, async (message) => {
  console.log(message);
});
console.log(bridge.snapshot);
const submission = await chat.submit({ type: "input", content: "Hello" }, ctx);
await submission.wait(ctx);
// On UI disconnect: await bridge.stop(). It does NOT terminate the chat or the harness.
```

Settings default off. Use `host.setReadout(chat, { enabled, emotion, workspace })` and `host.setSteer(chat, steerOrNull)`.
Asset IDs come from the serve's registry/health. Settings are rewindable per conversation and apply when the next model
request prepares, not mid-token. Steering strength is signed (`abs(strength) <= 2` on the serve); disabling readouts does
not clear steering, so call `setSteer(chat, null)` to do that. Workspace requires emotion readouts.

The bridge delivers Pi text/thinking/tool events, `readout.batch`, terminal `readout.turn`, versioned `settings`, and
`readout.error`. Initialize the client from `bridge.snapshot`, then consume events in order (buffer events arriving
during attachment until that snapshot is installed). Pi overflow snapshots replace the displayed transcript. A terminal
turn already contains its full records: do not concatenate those with its batches and double count them.

Readouts never enter the model's message context. A failed readout does not invalidate a successful text reply: surface
its `aborted`, `transport_error` or `contract_error` status separately. Persistence failures are delivered through
`readout.error` and retained in `host.errors`; they must not be displayed as successful readouts.

For reports/history, page `Conversation.entries({ order: "ascending" }, limit, cursor, ctx)` and select `TurnEntry`.
This is fork-aware full history; Pi's active-context snapshot is not a complete report source after resets/compaction.
Use complete turns for complete measures; show partial statuses and missing reasons explicitly. Emotion values are
direction alignments, and workspace values are interpreted lens scores—not evidence of feelings or proof of knowledge.

For orderly shutdown, finish or abort the conversations first, wait for the terminal records the client needs, stop
bridges, then `await host.close()` and `await harness.close(ctx)`. Stopping a bridge joins queued sends but drops unread
frames. Closing a host during a stream deliberately leaves its durable batches for reconciliation on the next request.

The keyless provider is intended for a trusted private endpoint. An internet-facing control plane still needs its own
authentication, conversation authorization, tool approvals and resource limits; this package does not add those.

## GPU integration check (coordinated, local only)

Do not run this while another GPU stage is active. Coordinate first, inspect PSI and `/tmp`, ensure no production
clients, and use the reviewed serve-down wrapper. The kernel checkout is read-only and pinned in `e2e-launch.py`.
The work path must not exist; every attempt keeps its private KV store, SQLite database, logs and result.

```sh
scripts/e2e-gpu.sh --check --work /home/adam/nomos_data/readout-spine/pi-e2e-UNIQUE
# After coordination and successful CPU checks, from a clean checkout:
/home/adam/Projects/glimmer-emotions/scripts/with_serve_down.sh \
  /home/adam/nomos_data/readout-spine/logs/pi-e2e-UNIQUE -- \
  systemd-run --user --wait --collect --pipe --unit=codex-pi-e2e-UNIQUE \
  -p WorkingDirectory=/home/adam/Projects/glimmer-agent -p RuntimeMaxSec=900 -p TimeoutStopSec=30 \
  -E PATH -E HOME /home/adam/Projects/glimmer-agent/scripts/e2e-gpu.sh \
  --work /home/adam/nomos_data/readout-spine/pi-e2e-UNIQUE
```

The isolated daemon/serve use port 8191. Success requires `PI E2E OK`, true exit zero, and production serve restoration,
not merely an HTTP response. The test asserts actual second-turn KV reuse, both readouts on every generated record,
two complete turn records through the bridge and no host errors. Never run it through `tools/sp1_env.sh`.
