# Gates: Glimmer Lab snapshot and steering-diff integration

Scope: fresh import on main; isolated review branch for recorded steering diff; no inherited Git history, capture data, builds, source edits or model calls.

- [ ] G1: the fresh snapshot preserves selected source files and records dirty-file provenance
  CHECK: node scripts/check-import.mjs --source
  EXPECT: SNAPSHOT VERIFIED
  EVIDENCE: pending

- [ ] G2: the imported application builds and its CPU-only contract/chat checks pass
  CHECK: npm run verify && npm run test:contract && npm run test:transport && npm run test:chat
  EXPECT: MUSE CHAT PASSED
  EVIDENCE: pending

- [ ] G3: all reachable commits use the Light of Baldr identity and no forbidden artifacts are tracked
  CHECK: node scripts/check-release.mjs
  EXPECT: RELEASE BOUNDARY VERIFIED
  EVIDENCE: pending

- [ ] G4: the integrated readout route passes its model and real-data tests without bundling capture files
  CHECK: npm run test:steering
  EXPECT: STEERING DIFF VERIFIED
  EVIDENCE: pending

- [ ] G5: chat and steering-diff navigation, desktop/mobile presentation and CSS isolation work
  CHECK: npm run test:integration
  EXPECT: LAB INTEGRATION VERIFIED
  EVIDENCE: pending

- [ ] G6: the private remote has the fresh main and review branch, with no merge
  CHECK: node scripts/check-remote.mjs
  EXPECT: PRIVATE BRANCHES VERIFIED
  EVIDENCE: pending
