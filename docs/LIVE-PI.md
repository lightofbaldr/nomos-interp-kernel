# Live Pi control plane

Live mode hosts the pinned glimmer-agent provider, extension and bridge in the
SvelteKit server. It does not copy those implementations. The dependency archive
and its source identity are recorded in `vendor/glimmer-agent.md`.

## Run locally

Use Node **24.22.1** with bundled Undici **7.30.1** (`nvm use` selects the
already-installed `.nvmrc` version). Set these in the server environment, not
browser configuration. See README for the checked production launcher:

```sh
nvm use
export NOMOS_API_BASE_URL=http://127.0.0.1:8091
export NOMOS_READOUT_REGISTRY="$HOME/nomos_data/readout-assets/registry.json"
export NOMOS_TOKENIZER_JSON="$HOME/nomos_data/muse-glimmer-tokenizer/tokenizer.json"
export NOMOS_TOKENIZER_SHA256=<the-operator-verified-tokenizer-json-sha256>
export GLIMMER_SESSION_DB="$HOME/nomos_data/glimmer-lab/sessions.sqlite"
npm ci --ignore-scripts
npm run build
npm start
```

Dev, preview checks and production start bind loopback by default. Production
`HOST` is an explicit operator override; Pi and Nomos control routes still require
a loopback Host and same-origin browser request. This is a single-user local
control plane, **not authentication for a public or shared deployment**. Do not
expose it through a reverse proxy without adding authentication and authorization.
Run one app process per session database. No model or serve is started by the app.

Readout metadata comes from `/health`'s advertised asset IDs joined to locally
hash-verified asset manifests. The bridge does not carry vocabulary. Token labels
are an explicitly configured, SHA-pinned local ByteLevel tokenizer; IDs remain
authoritative and unknown labels are never guessed. This disk identity does not
attest remote/GPU tokenizer bytes. If the registry or labels are unavailable, chat
can still work and the relevant controls/labels say unavailable.

## Sessions and events

- SQLite owns conversation history and versioned settings. Browser local storage
  remembers only the selected conversation ID. A new session leaves old sessions
  alive. Detaching/reloading the page stops its bridge, not generation. Stop is an
  explicit Pi abort. Only one generation is admitted by this process at once.
- After process restart, unfinished work is visible but not silently resumed by
  merely opening a viewer. Stop the pending conversation before a new submission.
  This prevents a new submission from implicitly scheduling an older pending run.
- SSE starts with `bridge.snapshot`. Ordered batches are deduplicated by request
  and position. Terminal records **replace**, never append to, streamed batches.
  Reconnect installs a fresh snapshot. EOF is not completion. Contract errors,
  transport errors, aborts and text stop reasons remain distinct.
- Settings apply to the **next request**, not an in-flight one. The saved version
  and actual turn header are separately visible. Steering is signed, −2 to +2;
  readout capture supports at most three emotion and four workspace layers.
- Selecting a different session initializes the Controls form from that session's
  saved settings. Same-session bridge updates do not erase unsaved edits. The
  saved readout line lists all layers; the form edits one layer at a time.
- Readouts describe consumed token positions, not SSE chunks or aligned text
  characters. Missing/reused/unavailable positions are never filled with zeros.
  Workspace ranks are only within the supplied top-k. Projections are signed raw
  alignment, not probabilities or evidence of feelings. Demo mode stays separate.

The initial session enables emotions at L20/24/29 when advertised; workspace is
off until explicitly enabled. Tools start disabled; the harness workbench can
enable the reviewed sandboxed tools. Automatic retries and compaction remain
disabled. The session cap is 100 and each submitted message is limited to 4,000
characters. The model reply cap is 6,144 generated tokens, including reasoning.

## Decoding and an explicit Retry

Greedy is the default, sent explicitly as `temperature: 0, top_p: 1, top_k: 0`.
The per-conversation **Glimmer's sampling** switch is off initially. Switching it
on saves `temperature: 1, top_p: 0.95, top_k: 64` for subsequent requests. This is
slower on the current serve because it disables speculative decoding. Earlier
Gold measurements without readouts were about 17 tokens/s sampled versus 40
greedy; these are not a speed promise for the current turn. Sampling can still
repeat. Nothing changes the serve or adds a loop detector.

Each Pi request snapshots the setting at its context cutoff, writes a non-model
`glimmer.generation.used` entry, and sends those exact parameters plus
`max_tokens: 6144`. The record identifies task, attempt, cutoff and, when capture
is active, the readout request. Chat, Workspace, Statistics and Interpreter show
the recorded decoding, never today's toggle for an old turn. Older turns and
recordings without this metadata stay explicitly unrecorded. Settings survive
restart, and a fork restores the value at its fork point. Concurrent or stale
setting changes refuse while a run is active. None of this metadata enters
Glimmer's context.

The last assistant reply offers **Retry** only when its stop reason is `length`,
it contains nonempty reasoning, and it contains neither answer text nor a tool
call. A click atomically starts a new Pi generation task and appends a
`glimmer.retry` audit entry that omits the old attempt from **model context**, not
history. The existing user question remains exactly once. The old attempt stays
visible as “Stopped at the reply limit, retried.” There is no automatic retry or
automatic switch to sampling; greedy may produce the same result again.

Retry uses the saved decoding/readout/steering controls and the active profile.
A profile already marked pending still applies only on the next ordinary user
submission, as before. A busy, answered, stale, already edited/retried, reset or
foreign target is refused. Admission uses native Pi 1.3.0 task/doc APIs and the
same global one-run restriction as ordinary Submit. Stop, disconnect and reload
retain their existing semantics.

The stateless `/api/nomos/chat` route enforces the same two fixed parameter sets
and cap; its optional `sampling` field must be a boolean. It does not accept
arbitrary sampler parameters. `X-Glimmer-Generation` records the exact parameters
in the response, alongside `X-Glimmer-Decoding` and `X-Glimmer-Reply-Cap`; this route
does not persist a conversation. Both paths have a bounded ten-minute request
timeout (formerly five), to avoid cutting a sampled 6,144-token reply short at
the former timeout. That is a time limit, not a guarantee that every reply will
reach the token cap. Automatic retries remain off.

## Observational alerts

The Alerts tab configures page-local rules and re-evaluates this conversation's
loaded records. It does not modify the model or produce a durable scientific
label. Emotion alerts are off by default; the reasoning workspace watch-list is
on at L44. The seed prefixes are ` can't`, ` cannot`, ` couldn't`, ` hallucin`,
and ` invent`. These are exploratory internal-signal alerts, **not a lie detector**.

Emotion crossings use **raw projection minus the running mean of all earlier
defined generated reasoning positions**, with the same direction asset, name and
layer in this conversation. There must be 32 earlier samples. The current sample
is tested before joining that mean. A crossing needs two adjacent eligible rows,
below then at/above the chosen positive difference (or the mirrored below rule).
The default difference of 5 is a UI starting value, not calibrated. A gap, missing
measurement, non-reasoning row or turn boundary resets crossing history; it does
not erase the prior mean. Steering/context changes can themselves change this
statistic. It is not a causal or significance claim.

Watch-list matching ignores case and trailing whitespace and matches isolated-token
prefixes. A leading space requires a space-prefixed token; a prefix without that
space also accepts a token starting directly with the letters. This is not a word
classifier: ` invent` still matches ` inventory`. It does not reconstruct split
words. An entry needs an adjacent known
reasoning row where that prefix was absent from the same supplied top-k. A missing
workspace, missing token label, gap, new turn or first observed row cannot supply
negative evidence. A new match after such a gap is not called an entry. Word-piece
splits may be missed. Token IDs and top-k ranks remain visible in badge details.

Badges identify their rule, turn and consumed position, and can select that row in
the inspector. Workspace badges name the matched workspace token, not the consumed
token. By default they retain the first entry per watched prefix per turn and count
later re-entries; the detailed entry trail is optional. The first matched token is
the grouped badge's label even if later matches use another token with that prefix.
Emotion crossings remain separate badges; only workspace entries are grouped.
No extra absence-duration filter is applied. Terminal records and reconnect
snapshots do not double-count. The latest 200 groups or detailed entries are shown,
with full counts for the loaded records. Grouped first positions and counts survive
eviction from the bounded detailed trail. Signals
from incomplete/error turns are explicitly provisional. Switching conversations
resets the statistical history; rules remain page-local until reload.

## Optional second opinion

The optional [Muse Wonder second opinion](META-ANNOTATOR.md) adds one explicit
button after a completed turn. It sends a bounded summary only on a click, saves
the note outside Glimmer's model context, and never retries automatically.

## Recorded steering catalog

The read-only default is `~/nomos_data/steering-diff/catalog-sampled-20261029`, not the prototype
repository. Override it with `GLIMMER_STEERDIFF_DATA_DIR`. It must contain the
catalog plus its SHA-bound dataset files; missing catalogs are explicitly refused.
No captures or build output are committed. `data-inventory.local.json` beside the
catalog is optional for serving but required by the local source/copy test.
The older `catalog` remains compatible with the preceding approved viewer.

## Verification

`npm run verify` checks Svelte and builds. `node --test --test-reporter=tap
tests/live/server.test.ts tests/live/model.test.ts` tests real Pi against a fake
kernel and the recorded Python-producer contract fixtures. `node
scripts/live-browser.mjs` checks desktop/mobile against that fake through the real
gateway. `node scripts/live-regression.mjs` runs the existing demo, recorded,
loopback and accessibility-oriented browser checks.

`npm run test:generation` covers decoding, durable attribution and replacing
Retry through real Pi with a fake kernel. After building, `npm run
test:generation-ui` checks the switch, reload/session switching, history and
inspection labels, replacing Retry and both direct-route parameter choices in
an isolated browser/server. It makes no model or paid call. Both are included
in `node scripts/runtime-regression.mjs`.

`scripts/live-e2e.mjs` is the explicit, real-GPU client check: it checks for existing
connections, uses the **already running serve**, and never starts/stops it. Do not
run it concurrently with another model client. Test evidence is local/ignored.
