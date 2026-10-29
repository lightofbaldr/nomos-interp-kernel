# Recorded steering diff

The review branch adds `/steering-diff` to the imported SvelteKit application.
Chat links to it from the desktop sidebar and mobile conversations drawer; the
explorer links back. It is a native route, not an iframe or a second live model
client. Navigating away detaches the chat viewer; the live Pi session continues
on the server. See `LIVE-PI.md` for session behavior.

The implementation is ported from the local-only prototype at
`b3a10582ed479d541c73a6cd2d8ad1f656d4eb3f`. Its source hashes are in
`docs/import/steering-scratch.json`; its `import_sha256` fields describe the
initial mechanical port, before the subsequent routing/theme/error-text and
strict-typecheck adaptations in this branch, not final file pins. No scratch
Git history was imported. The source hashes are import provenance, not current
file pins: the display adapter now also supports explicit samples, attributed
comparison labels and emotion-only exports.

## Local data, never a build asset

```sh
npm ci --ignore-scripts
npm run verify
GLIMMER_STEERDIFF_DATA_DIR=/path/to/local/catalog-directory \
  PORT=5241 ORIGIN=http://127.0.0.1:5241 npm start
```

The sampled-view default is `~/nomos_data/steering-diff/catalog-sampled-20261029`.
The earlier `catalog` directory retains its original three-dataset metadata so
older approved viewers remain compatible while this follow-up is under review.
The sampled directory holds a `glimmer.local-catalog/1` catalog, six index documents and the
byte-copied comparisons. The catalog's per-file hashes are checked on the server
and again in the browser. Only catalog-listed JSON files can be read; traversal,
symlinks, changed files and writes are refused. The adapter does not derive
values, contact a kernel, import a capture into the repository, or include it in
`build/`. The catalog is operator-owned, not a remote trust anchor or a signature.
Keep it and its files quiescent while reviewing.

Do not expose this unauthenticated private local application publicly. It can
serve provenance and recorded text to anyone who can reach it. Keep the bind
address loopback until a separate authenticated deployment is designed.
`npm start` binds `127.0.0.1` when `HOST` is unset or empty. An explicit `HOST`
is an opt-in to that address; the launcher does not add authentication. Use
`npm run test:start` after building to verify the real default listener. Direct
`node build/index.js` invocation bypasses this default.

The current catalog covers S1 (15 comparisons), tone prompts (60), recorded
lie scenarios (56), sampled Sietti fear/random controls (12), sampled Tathuga
fear (7), and selected Tathuga voices/random control (6). The sampled workspace
sets each include baseline-repeat and armed-zero integrity comparisons at s0.
Only index documents and the selected comparison are loaded; the comparisons
are not prefetched. Missing data gives an explicit unavailable state, not a
substitute fixture. The original captures and pre-addition catalog/inventory
are retained locally.

## Display contract

- **Matched input replay**: the same fixed token history in both arms. The
  reference text is not a new steered reply.
- **Free generation**: each arm's own continuation; paired readouts stop before
  divergence, and later text/token tracks are separate.
- **Sampled free generation**: the sample is part of selection and identity.
  Both arm metadata, run IDs and seeds must agree with the selected sample. A
  multi-sample arm requires an explicit choice; an arm with one exported sample
  shows that sole sample. No sample is silently substituted. Same-sample fear
  and random-control buttons sit together when both were exported.
- The prefill caption follows declared boundaries: a prompt-only prefill means
  each arm generates its own reasoning and answer; an answer-start prefill means
  recorded thinking precedes new answers. Missing boundaries stay unspecified.
- Comparison labels and grades are shown per arm, attributed to the supplied
  rater (Prime), with notes unchanged. They are not readout predictions or blinded
  ratings. A null label is not supplied, including integrity controls; the UI does
  not copy baseline labels into controls. The source label-file path is reported
  provenance, not independently verified by the browser.
- The curated caption is exploratory: random pushes can change outcomes too.
  This is not a demonstration of an emotion-specific honesty mechanism.
- Top-8 membership and ranks are scoped to the transmitted top 16. Exact deltas
  and bounds are grouped separately; a missing score is never zero. Formatting
  rounds bounds outward and preserves very small nonzero exact changes.
- Positions name the token just consumed. Prefix workspace values can be null.
  Channel jumps use supplied stepped-row labels, not inferred offsets.
- An emotion-only capture has workspace settings and rows explicitly null. It
  shows no workspace layer/rank controls and no substitute or zero workspace.
- L24 is the injection layer, not an independent downstream observation.
- Negative strength reverses that named direction, not a learned opposite
  emotion. Direction alignments and interpreted J-lens scores are not feelings,
  proof of knowledge, a topology measurement or a safety verdict.
- Recorded scenario provenance belongs to the source episode. Its historical
  `lie` / `overclaim` labels do not classify the new generated answers. Replay and
  answer scenarios link by explicit source identity, not their names.
- The producer reported repairing channel labels before exporting the lie
  bundles. The UI uses those supplied labels; it does not attest the relabelling
  from original capture arrays.

## CPU-only verification

Run `npm run verify` before the browser checks. `npm run test:steering` runs
display/data-server tests, verifies all current source/copy hashes, then runs the
ported base, synthetic-scenario, real-lie and sampled-bundle browser suites.
`npm run test:integration` checks native navigation, chat-style isolation,
portalled styling, desktop/mobile layout, unavailable data and local-only
traffic, then the imported UI/browser/contrast regressions. The source UI's
transport/chat suites use CPU fakes. Chromium runs with its GPU disabled.

The real-data check also reads `data-inventory.local.json` inside the catalog
directory. It is not a portable fixture: another
workstation must supply a matching private snapshot and inventory, or run only
the synthetic tests. No screenshots or scientific data from these checks are
committed. Local evidence lives in ignored `evidence/` and `preview/` directories.

This follow-up stays on the live-readouts review branch. Merging and publication
require Adam's decision after Prime's review.
