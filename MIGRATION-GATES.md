# Gates: Glimmer Lab snapshot and steering-diff integration

Scope: fresh import on main; isolated review branch for recorded steering diff; no inherited Git history, capture data, builds, source edits or model calls.

- [x] G1: the fresh snapshot preserves selected source files and records dirty-file provenance
  CHECK: node scripts/check-import.mjs --source
  EXPECT: SNAPSHOT VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=2132469ce15f782080a71ee5159a4f2064dd5a4e572218362937c7d5ccf1fdde; output-bytes=68

- [x] G2: the imported application builds and its CPU-only contract/chat checks pass
  CHECK: npm run verify && npm run test:contract && npm run test:transport && npm run test:chat
  EXPECT: MUSE CHAT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=c56977e30d06fffce25490788a942780820368506a1b3e83f4ff6fcb7054cb23; output-bytes=2787

- [x] G3: all reachable commits use the Light of Baldr identity and no forbidden artifacts are tracked
  CHECK: node scripts/check-release.mjs
  EXPECT: RELEASE BOUNDARY VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=25f465fcd767daf76c8b121567b96fb630202a56513af07b5015e8e36b5a484f; output-bytes=54

- [x] G4: the integrated readout route passes its model and real-data tests without bundling capture files
  CHECK: npm run test:steering
  EXPECT: STEERING DIFF VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=a8210c9f8bad247c8770be3066ccf70a3d9662d00cb649b9b3b6bdf55d006b04; output-bytes=2533

- [x] G5: chat and steering-diff navigation, desktop/mobile presentation and CSS isolation work
  CHECK: npm run test:integration
  EXPECT: LAB INTEGRATION VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=01a16e1c3dca5315d3aacf535f28bab8732c38edc21c8d9d4ae30a580103805a; output-bytes=590

- [x] G6: the private remote has the fresh main and review branch, with no merge
  CHECK: node scripts/check-remote.mjs
  EXPECT: PRIVATE BRANCHES VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=3ee0a67c0fe5/30 entries; EXPECT=matched; output-sha256=4b3edb0be89b597ee79ebf79820a306c0b1c12f3d4afbeb2c05e2582c50220da; output-bytes=108
