# Private-to-public export checklist

This repository is private. Before a public export, review (do not silently
rewrite historical provenance) the following:

- `docs/import/snapshot.json`: source checkout path, original private remote,
  original status listing and timestamps.
- `IMPORT.md`: source checkout and local service instructions.
- `scripts/harness.mjs`: personal Chromium executable fallback.
- `docs/ATTRIBUTION.md` and `docs/assets/PROMPTS.md`: personal image source path.
- Inherited `README.md`, `project.json`, `scripts/refresh-preview.mjs`, and test
  scripts: private LAN endpoint, source-preview port/session and local assumptions.
- Brand assets in `docs/assets/` and `static/`: review redistribution rights;
  an existing attribution is not a license grant or Meta endorsement.
- Any steering-diff adapter defaults, test paths, evidence or integration docs
  added on the review branch. Capture bundles must remain external and private.

Inventory again at export time with a tracked-file search for `/home/adam`,
`192.168.`, `design-kits`, private hostnames and local data paths. Also check the
vendored package license/notice files. This checklist is not publication approval.
