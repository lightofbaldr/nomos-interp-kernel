# Recorded steering diff

The review branch adds `/steering-diff` to the imported SvelteKit application.
Chat links to it from the desktop sidebar and mobile conversations drawer; the
explorer links back. It is a native route, not an iframe or a second live model
client. Navigating away from chat unmounts that chat view; this change does not
add persistent Pi sessions or preserve an in-progress conversation across routes.

The implementation is ported from the local-only prototype at
`b3a10582ed479d541c73a6cd2d8ad1f656d4eb3f`. Its source hashes are in
`docs/import/steering-scratch.json`; its `import_sha256` fields describe the
initial mechanical port, before the subsequent routing/theme/error-text and
strict-typecheck adaptations in this branch, not final file pins. No scratch
Git history was imported. The
comparison model and four presentation components retain their original bytes.
Integration changes are routing/navigation, a scoped stylesheet (including the
portalled dialog), the external data adapter, error wording and test harnesses.

## Local data, never a build asset

```sh
npm ci --ignore-scripts
npm run verify
GLIMMER_STEERDIFF_DATA_DIR=/path/to/local/catalog-directory \
  HOST=127.0.0.1 PORT=5241 ORIGIN=http://127.0.0.1:5241 node build/index.js
```

The private workstation default is
`/home/adam/Projects/glimmer-steering-diff-scratch-20261026/public/data`.
It holds a `glimmer.local-catalog/1` catalog, three index documents and the
byte-copied comparisons. The catalog's per-file hashes are checked on the server
and again in the browser. Only catalog-listed JSON files can be read; traversal,
symlinks, changed files and writes are refused. The adapter does not derive
values, contact a kernel, import a capture into the repository, or include it in
`build/`. The catalog is operator-owned, not a remote trust anchor or a signature.
Keep it and its files quiescent while reviewing.

Do not expose this unauthenticated private local application publicly. It can
serve provenance and recorded text to anyone who can reach it. Keep the bind
address loopback until a separate authenticated deployment is designed.

The current catalog covers S1 (15 comparisons), tone prompts (60), and recorded
lie scenarios (56). Only index documents and the selected comparison are loaded;
the 56 large lie comparisons are not prefetched. Missing data gives an explicit
unavailable state, not a substitute fixture.

## Display contract

- **Matched input replay**: the same fixed token history in both arms. The
  reference text is not a new steered reply.
- **Free generation**: each arm's own continuation; paired readouts stop before
  divergence, and later text/token tracks are separate.
- Top-8 membership and ranks are scoped to the transmitted top 16. Exact deltas
  and bounds are grouped separately; a missing score is never zero. Formatting
  rounds bounds outward and preserves very small nonzero exact changes.
- Positions name the token just consumed. Prefix workspace values can be null.
  Channel jumps use supplied stepped-row labels, not inferred offsets.
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
ported base, synthetic-scenario and real-lie browser suites.
`npm run test:integration` checks native navigation, chat-style isolation,
portalled styling, desktop/mobile layout, unavailable data and local-only
traffic, then the imported UI/browser/contrast regressions. The source UI's
transport/chat suites use CPU fakes. Chromium runs with its GPU disabled.

The real-data check also reads the original local snapshot's
`data-inventory.local.json` beside `public/`. It is not a portable fixture: another
workstation must supply a matching private snapshot and inventory, or run only
the synthetic tests. No screenshots or scientific data from these checks are
committed. Local evidence lives in ignored `evidence/` and `preview/` directories.

This is a review branch. Main remains the fresh snapshot import; merging and
publication require Adam's decision after Prime's review.
