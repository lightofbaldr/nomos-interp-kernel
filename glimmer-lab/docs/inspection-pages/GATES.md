# Gates: Statistics, Interpreter and System

Scope: make the three placeholder pages useful with saved Pi data and read-only system evidence, preserving the existing app. Node 24.22.1; no paid requests, kernel changes, or use of ports 8091/8093.

- [x] G1: statistics preserve unknowns, raw means, channel boundaries and recorded settings
  CHECK: node --test --test-reporter=tap tests/inspection/model.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=97f2e3ad35814972b0aa5015bc6d576cbd42e65e2a0c3b981075012454e6df65; output-bytes=1881

- [x] G2: system probes are bounded and cache requests are gated, deduplicated and fail safely
  CHECK: node --test --test-reporter=tap tests/inspection/system.test.ts
  EXPECT: # fail 0
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=b2921fe0923f9445ebdc1e3d369e11854adce337b34e83d8a12e6371bc793caf; output-bytes=1150

- [x] G3: Svelte and TypeScript validate all three pages
  CHECK: npm run check
  EXPECT: svelte-check found 0 errors and 0 warnings
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=baf970640a9fba0233edd434779f25219d5bf0e6645a232d9eaa82f8f19762cb; output-bytes=341

- [x] G4: production assets build successfully
  CHECK: npm run build
  EXPECT: ✔ done
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=8f8a7e418d3bdb3d55da3495b2c7fea669b36f7540211eafa45737b24ef277b8; output-bytes=31926

- [x] G5: all three pages work through navigation, saved history, reload, errors and narrow screens without a model call
  CHECK: node scripts/inspection-browser.mjs
  EXPECT: INSPECTION PAGES VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=718613ca3e2710c678ac1596e46ee7125c991e6e288ab8e7e3638ebc2228ed66; output-bytes=26

- [x] G6: chat, workspace, annotator and loopback controls retain their behavior
  CHECK: node scripts/inspection-regression.mjs
  EXPECT: INSPECTION REGRESSION VERIFIED
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/adam/Projects/glimmer-lab; path=f924c3aec103/35 entries; EXPECT=matched; output-sha256=64eaeeab917eedbd920b321f03e95b732812eda52ce603a0abb6ae9b276bd512; output-bytes=9835

- [x] G7: the 1440x900 gallery is legible, with distinct measured, missing and interpretation states
  EVIDENCE: Visually inspected preview/checks/{statistics-fixture,interpreter-complete-fixture,system-fixture}.png and mobile variants, plus all three evidence/inspection-pages/*-deployed.png captures at 1440x900. Aligned panel headings, no page-width overflow, raw versus difference columns distinct, workspace off and watch-list unknown rows explicit. Completed interpretation screenshot uses a fake provider, not a paid call.

- [x] G8: deployed UI serves the new pages on loopback using 8092, without changes to any kernel service
  EVIDENCE: evidence/inspection-pages/deployment.json at 2026-11-01T18:12:36.712Z: pass true, errors [], forbidden model/annotation actions [], endpoint http://127.0.0.1:8092, health ready true and readout supported true. Saved conversation 2 rendered; one explicit read-only cache sample succeeded. Restarted glimmer-lab-live.service only after all conversations reported idle; current UI PID 2234575. No kernel/service restart, generation, paid call or request to 8091/8093.
