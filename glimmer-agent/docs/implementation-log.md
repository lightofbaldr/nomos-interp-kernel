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

### Task 4

- Extension tests observed RED before implementation, then GREEN. All 44 CPU tests and typecheck pass.
- Includes the reviewed finalization barrier, one-transaction terminal/state update, inherited-batch reconciliation, retry/abort/restart, post-summary failures, and close-during-write-ahead admission race. Implementation required no API changes.

### Task 5

- Bridge tests observed RED before implementation, then GREEN. All 51 CPU tests and typecheck pass.
- Ordered snapshot/events/settings delivery, bounded backpressure and stop behavior, and fork-aware report traversal follow the reviewed plan. No API changes needed.

### Task 6 preparation

- Prime was notified (local-comms 1400) and explicitly released the GPU, with no concurrent CPU-heavy work. The GPU attempt is not yet recorded as passed.
- Launcher deviation: a fresh exclusively created run directory replaces deletion of the plan's reusable store/database. The shell delegates to a small Python supervisor for checked environment resolution, pinned kernel/library identity, PSI/tmp/port admission, bounded readiness and cleanup of only its own process groups. A separate systemd user unit remains inside with_serve_down, as the brief requires.
- The conversation retains every plan assertion, including real turn-2 KV reuse; it additionally writes its summary/counts to result.json. CPU witnesses cover bad route, existing run, pressure and failed environment resolution. Full suite: 52/52 plus typecheck.

### Task 6 result

- One attempt, clean source 028f684; private run `~/nomos_data/readout-spine/pi-e2e-20261026-plan3-a/`, wrapper logs `~/nomos_data/readout-spine/logs/pi-e2e-20261026-plan3-a/`.
- `PI E2E OK`. Own systemd unit `codex-pi-e2e-20261026a.service`, exit 0, 36.247 s, memory peak 8.4G with swap 0B. Wrapper exit 0. Serve down 15:32:13 CDT, command exited 15:32:49, production serve READY 15:33:12; health ready/spec/KV store open.
- Both turns complete: first reused [0,0], prompt [0,74], generated [74,401], 401 records; second reused [0,401], prompt [401,412], generated [412,668], 267 records. Every generated record has both readouts; host.errors empty. Bridge delivered 158 events. Signed steering is CPU-covered; this GPU check is the plan's unsteered two-turn test, not a steering efficacy experiment.
- Result SHA256 `868b3bce92c522a24ddd98a1c6df8a7d40a42347e2dce876d1e1f24493c820c6`; launch `2bf01fe06aaa25f27b1a27e907e09085941877442398548445b59e4ab3b5d203`; run.log `4ae2004bd8e36346717b02aa99da1b25b4419a3bb24948203e35df6db32985b8`; wrapper.out `9a889c144521125f6b66ddb20550d0811b85fa4161b9aa9f572d7f1445b3cfe2`.
- Production wrapper SHA256 `f2a3206e150ee09789122675f00441720d32fb939c243e339438d8f0773f3918`. Kernel remains clean at 5e4a100; library SHA256 `c660097f7bec116210c06ed67e83bc07139b8eea3da850cec7d45d9a1f0628f9`, unchanged. No kernel build, source/library edit or dependency install there.
- Prime notified of success and GPU release (local-comms 1402). No further GPU work.

### Final integration pass

- Added explicit fragmented-tool-call parity against the stock provider and a real SQLite close/reopen test (records, settings and signed steering retained; projections excluded from later model messages). Both pass without implementation changes.
- Expanded integration docs: independent chats versus the singleton root, lifetime and shutdown, reconnect ordering, report traversal, no duplicate counting, interpretation and deployment limits.
- Final fresh npm ci, package-hash check, 54/54 CPU tests and typecheck all pass. Acceptance G1-G5 re-executed; G6 uses the successful GPU evidence, G7 the branch/identity/handoff checks. Seven met, zero unmet, zero abandoned.
- Final source comparison: src/pi.ts, contract.ts, provider.ts, extension.ts and bridge.ts are byte-identical to their reviewed-plan blocks. No code or dependency change since GPU-tested 028f684.
- Full result: `~/Desktop/Codex/RESULT-readout-spine-plan3-pi-2026-10-26.md`, sent to Prime (1403). Merge remains pending Prime's review and Adam's OK, not an implementation task.
