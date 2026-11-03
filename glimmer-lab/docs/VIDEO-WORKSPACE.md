# Video workspace: first visual checkpoint

Base: `0dcb2bc`. This checkpoint implements the first part of the 2026-11-01 brief,
not the entire redesign. Prime reviews the branch; Adam decides the visual direction
and any merge before Statistics, Interpreter, System and new steering controls.

## Pages and preserved behavior

- `/`: minimal chat, a compact emotion strip, a controls dialog and per-turn links
  to the Global Workspace. Profile attribution and opt-in second opinions remain
  available under **Turn details & second opinion**. Nothing invokes Muse Wonder
  automatically.
- `/workspace`: recorded or saved Pi readouts. Pick a turn, channel and token;
  click holds the position, arrow keys and previous/next step through it. Search
  finds token pieces, not a reconstructed whole-word index. Both panes scroll;
  no captured positions or candidate lists are truncated to fit the viewport.
- `/workbench`: the existing instructions/skills/tools/import-and-diff editor.
  Sandbox and profile semantics are unchanged. Its existing tabs also expose the
  detailed signals, grouped alert trail, alert settings and status during this transition.
- `/steering-diff`: the existing explorer, unchanged and CSS-isolated.
- `/preview`: the previous all-in-one layout, retained for existing feature
  regressions and access to the detailed alert trail. Its scripted examples are
  **not** the new sidebar history. `/preview?mode=demo` is explicitly illustrative.

History comes from the server's durable Pi conversations (the existing 100-session
limit). Titles are the first stored user message, whitespace-normalized and capped
at 90 characters; no model is used. Empty sessions say “New conversation.” Reopening
or reloading restores the durable transcript and readouts. A page-local SSE bridge
stays mounted across the new pages; navigation does not abort generation. Native
navigation to the legacy layout or steering explorer detaches the browser bridge,
not the durable generation. Existing alert rules are still page-local.

`GET /api/pi?history=1` reads history without calling kernel health/capabilities.
Opening the recorded page directly does not activate live chat. Selecting Chat or
a saved conversation may check the configured kernel's health, but never submits a
model request merely for navigation. This is still a loopback-only, single-operator
application, not authenticated multi-user hosting.

## What the workspace means

The positions, channels, token IDs, candidate IDs, scores, ordering and missing
reasons are the saved data, not new inference. The selection is the **consumed
token position**: its J-lens candidates are read after consuming it, not claims of
thoughts or knowledge. Each layer's ranks are only within the supplied top-k.
Absence from that list is not a zero score or evidence of absence.

The default word-like filter hides special tokens, punctuation/numbers and likely
fragments using an explicitly heuristic token-label rule. It cannot certify whole
words: some fragments look word-like and some valid words are hidden. Counts state
what is hidden; switching the filter off reveals every supplied candidate with its
original rank. Unknown labels stay visible as raw IDs. Scores are displayed to two
decimal places, without rescaling or converting to probabilities.

The four recorded depths are L36/40/44/48, shown deepest to shallowest. A live turn
shows only its actually captured layers; the existing controls currently select
one workspace layer. A later setting cannot recover layers or positions that were
not captured. Missing rows remain “No workspace readout,” never zeros. A missing
header is “Steering not recorded,” not “off.” An explicitly selected missing turn
does not silently substitute the latest turn.

The Observatory layout inspired the held-token and depth comparison. Its Broadcast
and Decidedness statistics are **not** in the spine contract. Both say “Not measured”;
we do not derive full-vocabulary confidence or a calibrated broadcast estimate from
16 candidates. Adding those requires a defined producer field and units, not a UI
approximation. No kernel changes are requested for this first checkpoint.

## Recorded sources (external, never committed)

`src/lib/server/recorded-workspace.ts` allowlists two files under
`~/nomos_data/steering-diff/run-20261027T090425-ws-SIETTI/SIETTI-reason/runs/`:

| Display source | File | SHA-256 |
| --- | --- | --- |
| Sietti paper, unsteered | `base.s0.free.json` | `2db4c59e4a1deb6c6998856ede0f0dccb50f8771e151d85c660e80f0011553d6` |
| Sietti paper, afraid +0.5 | `fear_0.5.s0.free.json` | `0d9c76b55f604c455ff3153d2d9b513893c89e9fdbea4ca5adfdf925118bc4d7` |

Both are sample 0, from recorded sampled runs, not new video outcomes. The route
checks the source hash, bounded file size, no-symlink path, structure and the
recorded tokenizer identity. The tokenizer JSON under `~/nomos_data/muse-glimmer-tokenizer/`
is pinned to `c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d`.
It uses the existing isolated-token label decoder. The question is fixed catalog
metadata for these two captures. No user-chosen filesystem path is accepted.
Missing/changed sources refuse with “Recording unavailable.”

The adapter is read-only; data is loaded on selection, never bundled into the app.
It does not use `:8091`, the production session database, GPUs or paid APIs to read
these files. Original capture files remain untouched.

## Offline checkpoint

On Node 24.22.1:

```sh
npm run check
npm run build
node scripts/workspace-tests.mjs
node scripts/workspace-browser.mjs
node scripts/workspace-browser.mjs --recorded
node scripts/runtime-regression.mjs
```

The browser checks start isolated loopback servers and exit them afterward. The
default workspace check uses a fake kernel and temporary Pi database; screenshots
named `*-fixture.png` are **UI fixtures, not model results**. The `--recorded` run
uses the two real pinned captures, verifies displayed ranks/labels/scores against
the selected raw row, and makes no live-service or provider requests. Screenshot
outputs live under ignored `preview/checks/video-*`.

For Adam's read-only visual preview, use an unused loopback port, with the real
kernel endpoint explicitly disabled:

```sh
NOMOS_API_BASE_URL='' PORT=5241 ORIGIN=http://127.0.0.1:5241 npm start
# Open http://127.0.0.1:5241/workspace?recording=sietti-base
```

No preview server is left running by the tests. The dedicated Statistics,
Interpreter and System pages, richer steering design, a four-depth live capture
control, and a fresh live video-flow check are follow-ups after Adam's checkpoint.
Live checks still need Prime's slot; there is no new authorization to use `:8091`.
