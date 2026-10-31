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
Mojo test fixtures/runners and verification docs name the local compiler prefix.
Mojo tool results include that prefix and selected hashes. Vite embeds the app
build root for trusted broker resolution: rebuild on the target host, and scrub
runtime data/screenshots rather than treating paths as portable deployment data.

Inventory again at export time with a tracked-file search for `/home/adam`,
`192.168.`, `design-kits`, private hostnames and local data paths. Also check the
vendored package license/notice files. This checklist is not publication approval.
