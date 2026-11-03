# Workspace: the 48 Step 2 recordings

The Workspace source selector now includes **System-prompt study · 48 recordings**.
Choose an item, then one of its six setups. For example:

`/workspace?recording=step2-kind-ecfce718efca`

This is a saved-data viewer. It does not invoke a model, use the live asset
registry, rerun a classifier, or calculate new scientific scores. The two original
Sietti recordings and the saved Pi workspace path remain available.

## What the viewer means

- The task comes from `items.json`; system text and opener come from
  `conditions.json`. The scenario binds both texts by SHA-256. An opener precedes
  the task in the same user message. These are the study's setup texts, not the
  generic `identity.system` capture argument. Prompt token IDs are checked against
  the scenario as well.
- Each recording is sample 0, unsteered, T 1.0 / top-p 0.95 / top-k 64. The capture
  schedule is fresh prefill then M=1 decode, without DFlash or a KV store. The
  exact recorded timestamp and seed are in the expandable provenance.
- Classifier labels are supplied by Prime's pinned manifest, not asserted truth.
  Registered classifier and post-hoc C1+C2 sensitivity are always distinct. The
  fact-checker/Lens exception is visible even with provenance collapsed: C2's
  offset 764 skips the opening refusal. That offset is **not** the new task marker.
- The solid-highlight window is the registered **up to first 64 answer tokens**.
  The label reports its actual count (15–64 in this capture).
- The dashed marker and separate jump use `task-response-start.json`. They are
  post hoc and **not a decision time**. Their rule, author, character offset,
  zero-based answer-token index, anchor and skipped-paragraph snippets are shown.
  Kind/Prior Radiofabrikk starts at index 65, outside the registered window; the
  registered window never shifts to follow it.
- The registered statistic uses raw ranks 1–8 before word-like filtering. The
  viewer still exposes all supplied top-16 candidates with their original ranks,
  token IDs and raw scores. Unknown labels remain IDs; missing readouts are not
  zeros. Broadcast, ignition and entropy remain **Not measured**.
- The precheck evidence link is a separately pinned G1 result for one default
  scenario, not 48 repeat checks. Main-run G3/G7 are not presented as repeats.

## Input boundary

`src/lib/server/workspace-step2.ts` contains the fixed operator paths and all
eight metadata/evidence pins. The six handoff inputs are loaded and checked
together; the precheck's gates and identity are checked when its evidence is
requested. The manifest supplies the recording hashes. Its paths must be exactly
`capture-20261101T122747/<setup>-<item>/runs/base.s0.free.json`, with a complete,
unique 8×6 roster. Browser IDs can only select an allowlisted entry.

The reader rejects traversal and symlinks in the resolved path, opens the leaf
with `O_NOFOLLOW`, bounds reads to the checked size plus one byte, and checks the
hash of the bytes parsed. Caps: metadata 2 MB, one recording 16 MB, tokenizer
32 MB. Token labels use the captured tokenizer pin and the captured asset IDs,
never the current registry order. The catalog does not open the 48 recordings;
only the selected recording is read. No capture or tokenizer is copied into Git
or the build. Origin checks, no-store headers and generic error responses apply
to both data and evidence endpoints.

The browser requests the study catalog only after this source is selected. Saved
Pi views and the original recording choices do not depend on the study files.

This assumes an operator-owned, quiescent data tree. As with the existing local
viewer, the path checks do not claim protection against a hostile concurrent
writer swapping ancestor directories. No filesystem path is accepted from HTTP.
The supplemental anchors are producer annotations, hash-pinned and checked for
identity, index and coverage; the viewer does not reclassify their paragraphs.

## Offline verification

Use Node **24.22.1**, then:

```sh
npm ci --ignore-scripts
node scripts/step2-tests.mjs
node scripts/step2-assets.mjs
npm run check
npm run build
node scripts/step2-browser.mjs
node scripts/step2-regression.mjs
```

The unit fixtures are synthetic and independent of local data. The asset/browser
checks need the pinned local capture tree and tokenizer. The browser server has
no kernel base URL and uses an isolated session database. It checks exact ranks,
scores, window positions, the Lens warning, lazy selection, short-answer coverage,
the index-65 boundary, origin/path refusals, legacy selection and 1440×900 / mobile
layout. Screenshots and evidence stay under ignored `preview/checks/` and
`evidence/`. The regression runner uses synthetic providers, not the live serve
or paid services. Deployment is separate and waits for review/merge.
