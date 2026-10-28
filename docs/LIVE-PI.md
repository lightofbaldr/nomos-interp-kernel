# Live Pi control plane

Live mode hosts the pinned glimmer-agent provider, extension and bridge in the
SvelteKit server. It does not copy those implementations. The dependency archive
and its source identity are recorded in `vendor/glimmer-agent.md`.

## Run locally

Use Node 24. Set these in the server environment (not browser configuration):

```sh
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
- Readouts describe consumed token positions, not SSE chunks or aligned text
  characters. Missing/reused/unavailable positions are never filled with zeros.
  Workspace ranks are only within the supplied top-k. Projections are signed raw
  alignment, not probabilities or evidence of feelings. Demo mode stays separate.

The initial session enables emotions at L20/24/29 when advertised; workspace is
off until explicitly enabled. Tools, retries and automatic compaction are disabled
in this initial local chat host. The session cap is 100 and each submitted message
is limited to 4,000 characters. The model output cap is 2,048 tokens.

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

Watch-list matching ignores edge whitespace and case and matches isolated-token
prefixes; it does not reconstruct split words. An entry needs an adjacent known
reasoning row where that prefix was absent from the same supplied top-k. A missing
workspace, missing token label, gap, new turn or first observed row cannot supply
negative evidence. A new match after such a gap is not called an entry. Word-piece
splits may be missed. Token IDs and top-k ranks remain visible in badge details.

Badges identify their rule, turn and consumed position, and can select that row in
the inspector. Terminal records and reconnect snapshots do not double-count.
The latest 200 badges are shown, with full counts for the loaded records. Signals
from incomplete/error turns are explicitly provisional. Switching conversations
resets the statistical history; rules remain page-local until reload.

## Recorded steering catalog

The read-only default is `~/nomos_data/steering-diff/catalog`, not the prototype
repository. Override it with `GLIMMER_STEERDIFF_DATA_DIR`. It must contain the
catalog plus its SHA-bound dataset files; missing catalogs are explicitly refused.
No captures or build output are committed. `data-inventory.local.json` beside the
catalog is optional for serving but required by the local source/copy test.

## Verification

`npm run verify` checks Svelte and builds. `node --test --test-reporter=tap
tests/live/server.test.ts tests/live/model.test.ts` tests real Pi against a fake
kernel and the recorded Python-producer contract fixtures. `node
scripts/live-browser.mjs` checks desktop/mobile against that fake through the real
gateway. `node scripts/live-regression.mjs` runs the existing demo, recorded,
loopback and accessibility-oriented browser checks.

`scripts/live-e2e.mjs` is the explicit, real-GPU client check: it checks for existing
connections, uses the **already running serve**, and never starts/stops it. Do not
run it concurrently with another model client. Test evidence is local/ignored.
