# Private-to-public export checklist

This repository is private. Before a public export, review (do not silently
rewrite historical provenance) the following:

- `docs/import/snapshot.json`: source checkout path, original private remote,
  original status listing and timestamps.
- `IMPORT.md`: source checkout and local service instructions.
- `GATES.md` and `MIGRATION-GATES.md`: historical/source and current verification
  working-directory paths. Their evidence should not be mistaken for portable setup.
- `scripts/harness.mjs`: personal Chromium executable fallback.
- `docs/ATTRIBUTION.md` and `docs/assets/PROMPTS.md`: personal image source path.
- Inherited `README.md`, `project.json`, `scripts/refresh-preview.mjs`, and test
  scripts: private LAN endpoint, source-preview port/session and local assumptions.
- Brand assets in `docs/assets/` and `static/`: review redistribution rights;
  an existing attribution is not a license grant or Meta endorsement.
- Any steering-diff adapter defaults, test paths, evidence or integration docs
  added on the review branch. Capture bundles must remain external and private.

Review-branch additions with explicit personal paths: `src/lib/server/steering-data.ts`,
`tests/steering/model.test.ts`, `scripts/lab-integration.mjs` (a path-leak
negative assertion), `docs/STEERING-DIFF.md`, and
`docs/import/steering-scratch.json`. The browser helpers inherit the existing
Chromium path from `scripts/harness.mjs`. Runtime responses can include source
episode paths from the external data, which this source-only checklist does not
redact.

Workbench imports retain operator-selected absolute source paths and per-file
hashes in the private session database and preview responses. These are runtime
data, not committed source. Scrub them before exporting sessions/screenshots.
`GLIMMER_HARNESS_IMPORT_ROOTS` is operator configuration and must not be committed.
Mojo test fixtures/runners and verification docs name the local compiler prefix
`~/Projects/nomos-interp-kernel/.pixi/envs/default`: specifically
`tests/mojo/integration.test.ts`, `tests/mojo/sandbox_test.py`,
`scripts/mojo-browser.mjs` and `docs/HARNESS-WORKBENCH-C-VERIFICATION.md`.
The opt-in live runner uses `GLIMMER_MOJO_PREFIX`; retain that operator selection,
not a personal default, in a public export. Do not rewrite historical evidence.
Mojo tool results include that prefix and selected hashes. Vite embeds the app
build root for trusted broker resolution: rebuild on the target host, and scrub
runtime data/screenshots rather than treating paths as portable deployment data.

Inventory again at export time with a tracked-file search for the operator's home path,
`192.168.`, `design-kits`, private hostnames and local data paths. Also check the
vendored package license/notice files. This checklist is not publication approval.

The video checkpoint adds private local capture/tokenizer defaults and file hashes
in `src/lib/server/recorded-workspace.ts` and `docs/VIDEO-WORKSPACE.md`. Those paths
use `homedir()` but still identify private local data. Review the question/catalog
metadata, runtime payloads and screenshots separately. No capture bytes are in Git.

The inspection-pages ledger (`docs/inspection-pages/GATES.md`) records private
verification paths, the local UI PID and backend port. System snapshots and
screenshots include the UI hostname, platform and resource readings; they remain
under ignored `evidence/` and `preview/checks/` and require a separate export review.

The Step 2 viewer adds private study paths and pins in
`src/lib/server/workspace-step2.ts`, plus provenance in
`docs/WORKSPACE-STEP2.md` and `scripts/step2-assets.mjs`. Review the questions,
setup texts, labels, marker anchors and evidence JSON in runtime responses and
screenshots before export. These are external capture data, not committed assets.
The private capture root is `~/nomos_data/sysprompt-study-20261101`; the tokenizer
is `~/nomos_data/muse-glimmer-tokenizer`. Neither requires a public default.

The recorded Harness² view adds private run paths, task IDs and evidence pins in
`src/lib/server/recorded-harness-config.ts`, its real-asset tests and
`docs/WORKBENCH-RECORDED.md`. Runtime payloads contain full recorded tool outputs,
assistant text, submitted source and historical harness files. Review those and
the screenshots separately before any public export. They are intentionally not
committed; the page's read-only label is not an authorization to publish them.

Agent-mode tests and the opt-in budget runner add private compiler, Python,
tokenizer and kernel-protocol paths in `scripts/agent-budget-e2e.mjs`,
`scripts/agent-browser.mjs` and `tests/agent/prompt-count.test.mjs`. Operator
manifests and measurement snapshots contain source pins and fresh conversation
content; retain them privately and review before export. Workspace file names
and text are private runtime data. No workspace or model-output bytes are
committed as part of Agent mode.

The separately operator-disabled shell adds the same private compiler prefix
to `scripts/shell-browser.mjs`, `tests/shell/integration.test.ts` and the real
namespace controls in `tests/shell/sandbox_test.py`. All their prompts/canaries
are synthetic. Review shell transcripts separately: arbitrary command text,
workspace paths and output can be private even though no host credentials are
passed. Never commit or publish an operator shell flag or live enablement as if
it were the default deployment.
