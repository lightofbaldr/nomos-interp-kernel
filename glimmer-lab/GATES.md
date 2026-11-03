# Gates: Nomos Muse chat, inspection and steering-preview frontend

OWNS: src/**, scripts/**, tests/**, docs/**, static/**, vendor/**, preview/**, package.json, package-lock.json, tsconfig.json, svelte.config.js, vite.config.ts, tokens.css, README.md, project.json, .gitignore, GATES.md

Scope: connect the existing frontend to Adam's already-running Glimmer at a server-configured endpoint, with streamed answers and actual reasoning, cancellation, multi-turn history, health and between-turn KV statistics. Preserve explicit demo/recorded modes; workspace/emotion data and steering remain unavailable. No kernel edits, kernel builds or model-service restarts. Only the owned UI preview is restarted. Bounded real chat acceptance is permitted; all failure controls use an isolated fake backend.

- [x] M1: Svelte type-check and production build succeed with zero type errors/warnings
  CHECK: npm run verify
  EXPECT: MUSE BUILD PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=0a6711ad359ea7bee394e40f491cd754dd85d8064c02cb6add68c41f0173cd65; output-bytes=102

- [x] M2: trace parsing accepts correct fixtures and rejects invalid identity, dimensions, scores, order, bounds and unsafe provenance assumptions
  CHECK: npm run test:contract
  EXPECT: MUSE CONTRACT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=3f1b7c49cbcab21aeabefda008748f4b238b351ec7c44da4cff3ee9f70dca169; output-bytes=2407

- [x] M3: chat/replay, cancellation, freeze/scrub, scenario selection, import/export, missing-live states, keyboard dialogs and latched steering-preview toggles work in an actual browser; toggles never modify output/readouts/exports or claim application
  CHECK: npm run test:browser
  EXPECT: MUSE BROWSER PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=caf9278a8a5f45a98b612e45d747ab1e98fdefbfbc05e943a5dadbe5ba85706b; output-bytes=132

- [x] M4: dark interface is usable at desktop/tablet and 320/375/414 widths; seven Dock targets are at least 60px with full labels, hover/focus tooltips and magnification work, reduced/narrow/touch modes stay static; browsing makes no model/registry/remote-asset calls
  CHECK: npm run test:ui
  EXPECT: MUSE UI PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=18bbc29198ac1e371713af309ef84df96b8d076a475e03392af25008dc00de12; output-bytes=118

- [x] M5: runnable local preview serves the current application rather than a stale build and passes the behavior/Dock checks
  CHECK: npm run refresh:preview && npm run test:live
  EXPECT: MUSE LIVE PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=6eaf8068fac4fdd1796d57ecb2b7deb4ba89e33344ba9c1a2c736af7b33665b3; output-bytes=314

- [x] M6: component provenance, research scope, future steering acknowledgment boundary and production limitations are documented; current screenshots show chat/composer/Dock without a fabricated steering effect
  EVIDENCE: manual 2026-10-16; refreshed desktop demo, 320px live welcome and actual 1440px/375px kernel-chat captures visually inspected. Composer/Dock remain in view, labels and source boundaries stay explicit, and reasoning can be expanded separately from the answer. Local AI Elements, Magic and Bits provenance reviewed; no shared-library edits. The mascot remains decorative and steering remains not applied. Historical Lighthouse results are still labelled as pre-mascot/title/Dock observations, not measurements of this live adapter.

- [x] M7: computed text contrast and foreground/focus token pairs meet their declared thresholds, including selected Dock and tooltip states
  CHECK: npm run test:contrast
  EXPECT: MUSE CONTRAST PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=c689e1454c9a4376e0aa5fdfd722e9fba5ebf1a3054ebe647068f2030fd2eb6e; output-bytes=134

- [x] L1: the same-origin proxy uses only a server-configured upstream, bounds and validates chat input, preserves SSE, and rejects origin/URL injection
  CHECK: npm run test:transport
  EXPECT: MUSE TRANSPORT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=d32d353a92c8e1634b4e4c62fde9a1bf04a8a8b7391f983b3ea131a2153adb47; output-bytes=158

- [x] L2: actual-browser fixture tests prove separate streamed reasoning/answer, exact multi-turn history, cancellation, interrupted-stream errors, offline handling and truthful KV/readout state
  CHECK: npm run test:chat
  EXPECT: MUSE CHAT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=def99aa3ced646541af1cdd9d593d5c27632e00ab5ac25a38f7b04a9c8ba7be1; output-bytes=124

- [x] L3: the managed preview connects to the supplied Glimmer and a bounded real prompt yields reasoning plus Paris with natural termination and real KV counters
  CHECK: npm run test:kernel
  EXPECT: MUSE KERNEL CHAT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=fe89ecd9aadad501b4df7edba746cc9bd0489809df3b0ded1cf174357f7ae960; output-bytes=188

- [x] L4: live conversation/composer/Dock/inspector remain usable on phone and desktop, with unavailable science data distinct from chat health and measured counters
  CHECK: npm run test:chat
  EXPECT: MUSE CHAT PASSED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=~/Projects/design-kits/ports/nomos-muse; path=cd58de0eff7b/40 entries; EXPECT=matched; output-sha256=def99aa3ced646541af1cdd9d593d5c27632e00ab5ac25a38f7b04a9c8ba7be1; output-bytes=124

- [x] L5: live transport configuration, evidence scope and remaining limitations are documented and actual live screenshots reviewed
  EVIDENCE: manual 2026-10-16; README, INTEGRATION, REVIEW, ATTRIBUTION and project metadata reconciled with the implemented fixed-upstream proxy, exact raw history, fixed reasoning-high system message and between-turn cumulative counters. Actual kernel-chat desktop/375px screenshots show Paris, Show reasoning, live source, measured KV counters and Preview/Not applied Dock; the separately saved expanded-reasoning capture and kernel-chat.json retain real nonempty reasoning and finish=stop from nomos-muse-glimmer-30b. CPU fixture layouts are separately labelled, not used as model evidence. Browser timing is not token throughput; health is server-reported, not binary attestation. Memory-only UI history, kernel-side cache persistence, private-LAN/no-auth limits and missing workspace/emotion/steering transport are explicit. No kernel changes, model launches, model-service restarts or public deployment.
