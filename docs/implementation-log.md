# Plan 3 implementation ledger

2026-10-26. Source: reviewed readout-spine Plan 3 and rulings in glimmer-emotions; current brief overrides repo, branch, identity and kernel path.

## Setup and cross-plan audit

- GitHub repository lightofbaldr/glimmer-agent is private and empty; no Forgejo writes.
- Repo-local author: Light of Baldr <oss@lightofbaldr.dev>. Task 1 scaffold may land on main; all later work on codex/plan3-pi, not merged.
- Kernel main 5e4a100c137bc01b42ba44c2cde473ad435711c0 is clean and read-only.
- Inspected pyhost/readout.py, serve.py, test_readout_serve.py, tools/readout_e2e.py and the recorded e2e/result.json. Required IDs, contiguous ranges/counts, missing reasons, echoed settings/steering and off-mode summary match the amended plan. Workspace refusal is an explicit null plus workspace_unavailable. Actual cross-language fixtures follow in Task 2.
- Fresh CPU-only serve suite: 18/18 passed under Python 3.12.13, CUDA hidden and bytecode disabled. No engine loaded.
- Runtime Node is v24.18.0 in this shell, not the brief's v24.22.1; it matches the reviewed witness environment and exceeds the minimum.
- The referenced Superpowers skills are unavailable. Using task-by-task execution and a solo acceptance ledger, without delegated agents.
- Preserve the plan's supplied code attribution; use conventional task commits rather than its long subject examples.

## Tasks

Implementation and verification evidence will be appended per task. GPU work is Task 6 only and requires a message to Prime first.

### Task 1

- Installed all five exact pins and locked transitive dependencies. pi-ai and pi-durable package.json hashes exactly match the audited tarballs; chord is 1.3.0. Reproducible verifier in scripts/verify-vendor.mjs.
- Three real Pi premise tests pass: mid-run display-only transaction and fork boundary, awaited afterResponse plus session/message identity, and pre-provider abort ownership. TypeScript passes unchanged.
- npm test explicitly selects TAP, making the plan's test-count oracle independent of terminal detection. Dependency install reported zero vulnerabilities; npm blocked two transitive install scripts, neither needed by these tests (not enabled).

### Task 2

- Plan's contract tests observed RED (missing implementation), then GREEN. All 16 CPU tests and typecheck pass.
- Added 13 fixtures emitted by the actual built Python serve (base/spec all, reuse, stripped BOS, budget, workspace unavailable, plus metadata-only, signed steering-only and emotion-only), with producer file hashes and kernel commit. Every fixture passes TurnCheck unchanged. Negative witnesses alter an ordinary completion ID or reused count and refuse.
- Included the real e2e record's summary-only response and range/count checks. result.json is an aggregate, not full raw SSE; the CPU producer fixture supplies that missing coverage. No cross-plan mismatch found, and no kernel change needed.
- The generator writes JSON only to stdout, fixes completion timestamps and uses CPU fake engines. Tests consume a checked-in fixture and need no Python, tokenizer or kernel checkout.

### Task 3

- Provider tests observed RED before src/provider.ts existed, then GREEN. All 25 CPU tests and typecheck pass with the pinned real Pi packages.
- The provider implementation follows the reviewed plan: stock stream, caller hooks first, awaited intake, one terminal handoff, session/last-message matching to avoid compaction theft, signed steering unchanged. No API-signature deviation needed.
