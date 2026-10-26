# Gates: readout-spine Plan 3

OWNS: src/**, test/**, scripts/**, docs/**, package.json, package-lock.json, tsconfig.json, README.md, GATES.md, .gitignore

Scope: Implement all six tasks on a review branch in private GitHub lightofbaldr/glimmer-agent. Kernel main is read-only; notify Prime before Task 6. No merge.

- [ ] G1: The built serve's cross-plan contract is covered by an independently generated CPU fixture and real-shape checks.
  CHECK: node --test --test-reporter=tap test/cross-plan.test.ts
  EXPECT: # fail 0
  EVIDENCE: pending

- [ ] G2: Installed exact Pi versions match the audited package.json hashes and the lifecycle premise tests pass.
  CHECK: npm run verify:vendor && node --test --test-reporter=tap test/verify-pi.test.ts
  EXPECT: # fail 0
  EVIDENCE: pending

- [ ] G3: Runtime validation and stock-provider parity cover valid and failing streams.
  CHECK: node --test --test-reporter=tap test/contract.test.ts test/provider.test.ts
  EXPECT: # fail 0
  EVIDENCE: pending

- [ ] G4: Durable lifecycle, fork, continuity, abort, close races and bridge ordering tests pass together.
  CHECK: node --test --test-reporter=tap test/extension.test.ts test/bridge.test.ts
  EXPECT: # fail 0
  EVIDENCE: pending

- [ ] G5: The complete CPU suite and TypeScript check pass from the review branch.
  CHECK: npm test && npm run typecheck
  EXPECT: # fail 0
  EVIDENCE: pending

- [ ] G6: Task 6 is coordinated with Prime, isolated from production, passes its real two-turn Pi bridge assertions, and restores the serve.
  EVIDENCE: pending; manual because this launches shared GPU work only after coordination and health checks.

- [ ] G7: Every commit has repo-local Light of Baldr identity, the review branch is pushed without merging, deviations and evidence are reported to Prime, and the kernel checkout is unchanged.
  EVIDENCE: pending
