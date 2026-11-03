# Gates: readout-spine Plan 3

OWNS: src/**, test/**, scripts/**, docs/**, package.json, package-lock.json, tsconfig.json, README.md, GATES.md, .gitignore

Scope: Implement all six tasks on a review branch in private GitHub lightofbaldr/glimmer-agent. Kernel main is read-only; notify Prime before Task 6. No merge.

- [x] G1: The built serve's cross-plan contract is covered by an independently generated CPU fixture and real-shape checks.
  CHECK: node --test --test-reporter=tap test/cross-plan.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/glimmer-agent; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=3d40ec316430a7c92cf2540ae58e3de1c3ba29b4cb251f2a20e4ee1759d6fcae; output-bytes=916

- [x] G2: Installed exact Pi versions match the audited package.json hashes and the lifecycle premise tests pass.
  CHECK: npm run verify:vendor && node --test --test-reporter=tap test/verify-pi.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/glimmer-agent; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=f729aab968ae03e757782f9273ec5d9d6d70fadcb1383b1b644bb8f229cc1fbc; output-bytes=1465

- [x] G3: Runtime validation and stock-provider parity cover valid and failing streams.
  CHECK: node --test --test-reporter=tap test/contract.test.ts test/provider.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/glimmer-agent; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=ffab501cb786c1ea978f1a9e076afe0851bf6d502a01dec36d09d82b0cce8ebb; output-bytes=5476

- [x] G4: Durable lifecycle, fork, continuity, abort, close races and bridge ordering tests pass together.
  CHECK: node --test --test-reporter=tap test/extension.test.ts test/bridge.test.ts test/sqlite.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/glimmer-agent; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=b944b6309bc993a61be5fdadc7f93a232be57796cb7c27ae0938fe8c723e7aba; output-bytes=8106

- [x] G5: The complete CPU suite and TypeScript check pass from the review branch.
  CHECK: npm test && npm run typecheck
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/glimmer-agent; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=8b1e8b0c4644cc5a77c460e3c54e07252070d2d969cbc9080679f8d99496f3bf; output-bytes=15782

- [x] G6: Task 6 is coordinated with Prime, isolated from production, passes its real two-turn Pi bridge assertions, and restores the serve.
  EVIDENCE: 2026-10-26, source 028f684, local-comms 1400 coordination and 1402 GPU release. Separate systemd user unit inside with_serve_down; run.log sha256 4ae2004bd8e36346717b02aa99da1b25b4419a3bb24948203e35df6db32985b8 contains PI E2E OK; unit and wrapper exit 0. Result 868b3bce92c522a24ddd98a1c6df8a7d40a42347e2dce876d1e1f24493c820c6: 401/267 complete records, second turn reused 401, both readouts on every generated record, no host errors. Production serve READY 15:33:12 CDT and health ready/spec/KV store open. Paths and full identity in docs/implementation-log.md.

- [x] G7: Every commit has repo-local Light of Baldr identity, the review branch is pushed without merging, deviations and evidence are reported to Prime, and the kernel checkout is unchanged.
  EVIDENCE: 2026-10-26 git log confirms Light of Baldr <oss@lightofbaldr.dev> for every commit; private GitHub origin confirmed. ls-remote confirms implementation tip 4392814 on codex/plan3-pi and main still 34da768. All five src files match the reviewed plan byte for byte. RESULT-readout-spine-plan3-pi-2026-10-26.md written on the desk and sent to Prime in local-comms 1403; final ledger tip follows separately. Kernel clean 5e4a100 and c660097f library hash rechecked after GPU and implementation. No merge.
