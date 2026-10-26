# Proposed chat and inspection integration

The chat/KV adapter is implemented against [KERNEL_CONTRACT.md](KERNEL_CONTRACT.md). The inspection frame format remains a **frontend proposal**, not a claim that the Nomos kernel exports these fields. Adam and Prime decide the real workspace/emotion probe and steering interfaces. The frontend does not launch or provision a model.

## Independent channels

Chat and telemetry remain independently observable. Missing workspace/emotion probes never stall chat, fabricate a score or imply an answer is safe. Live Stop aborts the browser fetch and the proxy's upstream stream; demo Stop only cancels local replay. Inspection freeze/scrub applies to demo/recorded frames, not live chat chunks.

The live adapter consumes actual `content` and `reasoning_content` deltas separately. It preserves exact whitespace/Unicode, including partial stopped replies, and sends those raw strings with the whole conversation on the next turn. Its fixed system message includes reasoning strength high and no changing date. A completion requires both a finish reason and `[DONE]`; premature EOF is an interrupted reply. No automatic regeneration/retry can silently duplicate a turn.

## Implemented transport

- Private server environment: `NOMOS_API_BASE_URL`, a fixed HTTP(S) origin with no credentials/query/path. Browsers cannot supply an upstream URL, choose arbitrary routes or forward authorization headers.
- Same-origin UI routes: `GET /api/nomos/health`, `GET /api/nomos/kvstore`, `POST /api/nomos/chat`. Chat POST requires a matching Origin and JSON, bounds the body to 256 KiB, 64 messages and 64,000 UTF-16 code units, and forwards only text/reasoning history. Per-message composer cap is 4,000 characters. The kernel remains authoritative for token/context limits.
- Upstream: `/health`, `/v1/kvstore/stats`, and streaming `/v1/chat/completions`; temperature 0, `enable_thinking: true`, max output 2048. Health/stat calls time out after five seconds; a chat proxy request is bounded to five minutes. Redirects are refused. Chat failures never become empty successful replies.
- The client bounds a streamed response to 4 MB and a pending frame to 256,000 characters. HTTP errors, malformed fields, unsupported tool calls and missing termination are explicit. Model text is escaped plain text; no model HTML is executed.
- Store counters are measured cumulative server totals, not per-conversation attribution. Current bytes use B/MiB/GiB; RAM/NVMe restores and evictions retain their actual units. Off/failed stores are unknown, not zero. Snapshots are held while generating; counters are fetched before/after turns and by idle health refresh only. No live stream delta is counted as a model token.
- Timing is browser-observed first streamed text and request duration, not kernel GPU time, token count or tok/s. Health model/spec fields are server-reported and are not loaded-binary attestation.
- Chats are memory-only and clear on reload, new conversation or leaving live mode. No tools, attachments, persisted account history, auto-titles, transcript uploads, public authentication or rate-limiting are implemented. The proxy is for the private local preview, not a public API gateway.

## Recorded file contract

`src/lib/trace.ts` is the authoritative TypeScript/runtime validator for `nomos.inspection.v1`. Example traces can be exported from the demo. The schema contains:

| Field | Meaning |
| --- | --- |
| `schema` | Exact version `nomos.inspection.v1` |
| `source` | `demo` or `recorded`; an uploaded file cannot claim `live` |
| `runId`, `modelId`, `title`, `capturedAt` | Self-reported record identity and capture timestamp |
| `probe.id`, `.method`, `.version` | Name the actual probe/lens artifact/method |
| `probe.scoreKind` | `cosine` only in this version; signed values within [-1,1] |
| `probe.calibration` | Description or explicit null; absence does not become a probability |
| `probe.layerIds` | Unique, ordered IDs, matched exactly by every score vector |
| `probe.kernelSha256` | SHA-256 or explicit null, still self-reported rather than attested |
| `messages` | One ordered user/assistant pair with plain text |
| `frames[].sequence` | Strictly increasing frame identity |
| `frames[].tokenIndex` | Nonnegative, nondecreasing producer-supplied generation position |
| `frames[].elapsedMs` | Nonnegative, nondecreasing relative elapsed time, not throughput |
| `frames[].textOffset` | Nondecreasing UTF-16 code-unit offset into the assistant text; not token count |
| `frames[].concepts` | Labels plus one signed cosine per declared layer |
| `frames[].emotions` | Labels plus signed raw scores, not a distribution |

Caps: 2 MB file, 1,000 frames, 64 layers, 24 concepts and 16 emotion labels per frame, 50,000 characters per message. Unknown fields, duplicate identities, malformed metadata, nonfinite/coerced scores and mismatched vector dimensions are refused. Empty concept/emotion lists mean no readouts supplied, not measured zero. The format is deliberately plain data; there is no HTML, tool invocation, component schema or authorization field.

An imported file remains **Imported / unverified**, even if it contains a plausible model ID or hash. Demo layers, positions, timestamp, elapsed values and scores are synthetic UI fixtures. Nothing in the parser verifies a trained lens, a true timestamp, the producer's honesty, the currently loaded binary, or whether a score causally explains a response.

## Emotion-steering controls: preview boundary

`EmotionDock.svelte` holds independent Joy/Calm/Sadness/Fear/Anger/Desperation/Compassion selections in component memory. They survive chat changes until toggled off or cleared, but reset on reload. All selections are explicitly **not applied**, including in disconnected mode. They have no transport and do not alter answers, readouts, the decorative mascot or exported `nomos.inspection.v1` data. That read-only file format is not a steering command or an intervention record.

The eventual adapter must keep **desired targets** distinct from **kernel-acknowledged application**. It must not mark steering active because a button was clicked or an HTTP request was accepted. Applied state needs the actual serving instance, model/vector artifact identities, intervention revision and effective token boundary. The backend's chosen layer, strength and multi-target composition policy must be explicit rather than inferred from emotion names. No vectors, strengths or compatibility guarantees are invented in this preview.

Record acknowledged start/stop boundaries and applied configuration with the corresponding generation so baseline/steered outputs can be compared. Make refusal, pending changes, failed removal, disconnection and unknown application state visible. Clearing desired targets is not proof that the kernel stopped steering. Do not auto-apply old preview selections when a real transport first connects. Adam and Prime own the real intervention contract; the UI does not implement activation discovery, recording or modification yet.

## Before live integration

1. Confirm the real probe artifact and geometry, the score definition, layer/token coordinates and any calibration. Do not borrow Claude results as Muse validation.
2. Agree on request/run IDs joining chat deltas to telemetry, a server-instance ID, monotonic sequence numbers, ordering and truncation/drop reporting. Never join by arrival time alone.
3. Have the serving process report its loaded model/kernel/probe identities. Reading a source registry or hashing a disk file from the UI process is not serving-process attestation.
4. Specify transport (SSE/WebSocket), authentication/origin policy, sample cadence, history/memory bounds and reconnect behavior. No browser provider keys or unrestricted URL proxy.
5. Keep probe failure visible and distinct from zero. If sampling skips positions or drops frames, say so. The graph's edges must not be presented as causal unless the producer supplies measured causal evidence.
6. Validate against outcome-based safety tests, including the control that would refute the claimed safety value. A lens view or an emotion score alone cannot establish safety.

KV session-store counters are now connected through the real stats endpoint. GPU occupancy, device traffic, per-token kernel timing and per-request cache attribution remain unavailable; the UI does not derive them from fixture timing or SSE chunk counts.

## Reference boundaries

[The workspace paper](https://transformer-circuits.pub/2026/workspace/index.html) develops the Jacobian lens and a workspace-like representational account. It does not establish that a generic activation display or this UI is a validated global-workspace readout for Muse.

[The emotion-concepts paper](https://transformer-circuits.pub/2026/emotions/index.html) studies emotion concepts and their functional role. Its [primary preprint](https://arxiv.org/abs/2604.07729) describes functional emotions rather than demonstrating subjective feelings. This UI keeps that distinction explicit.

The [hackathon event](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) supplies the project context, not a certification or endorsement. No eligibility, submission-compliance, prize or event-performance claims are made.
