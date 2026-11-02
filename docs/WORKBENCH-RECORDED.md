# Recorded Harness² comparison

`/workbench/recorded` is a read-only view of **Run 2 (2026-10-31)**, selected for
the video. It is distinct from the live profile editor at `/workbench`.
No optimization, inference, compilation, model call or tool execution takes
place when it opens. It does not attach to Pi, even if a `conversation` query
parameter is supplied; navigating here detaches a mounted client bridge without
aborting its server-side work. The production session database is not opened by
this view. Normal navigation back to chat/editor still works as before.

## What to show

1. Keep **Recorded run · No live generation** and the ten-task strip in frame.
   This particular run has base **1/10**, primary improved **8/10**. Every task
   is shown, including the two different no-program failures. Each no-program
   rollout has 32 recorded model calls; its reason field is null, not invented.
2. The pinned example is `generics-largest-element`. Base and Improved share the
   same task, model/runtime identity, temperature 0, high thinking and 32-call
   cap. Each has its own full harness identity, source and grader record.
3. Click **First source write** on Base and **First compiler feedback** on
   Improved. Step with arrows or choose any saved tool exchange. Base has 6,
   Improved 15; none are hidden. The two event counters are independent, not
   aligned wall-clock time. There is no autoplay, streamed typing or fake delay.
4. Inspect **Last self-check**: it says “CHECK INCOMPLETE: compiled, but no code
   ran.” The agent received three compiler errors and revised its source, but
   did not run its own final program. Expand **External grader** to show the
   separate later compile exit 0, run exit 0, stdout `ok`, and pass. Base’s
   submitted program fails compilation; no run occurs. These are not the same
   events, and a tool result is not presented as a grader pass.
5. Expand **Final submitted source** and select a file under **Changes**.
   Changes is an exact file inventory with full before/after text and hashes,
   not a model-generated account. Absent and empty files are distinct. The
   source shown is the final submission, not a reconstruction at the selected
   event. Reminder text injected by the historical harness remains in tool
   outputs. Assistant reasoning and the final answer are separately inspectable.

## Scope and claims

- Harness² altered the surrounding instructions, skills and helper tools, not
  Glimmer’s weights. Claude/Opus was the improver, Glimmer the executor. The
  compile/run grader determines the counts; Muse’s quality opinions do not.
- The primary is `simd-fixed-dot-product`’s agg1 under the registered fallback
  rule, not the best of a heldout-score search. All ten outcomes come from the
  two pinned operator-driver aggregate files. The detailed selected pair is
  additionally joined to its own completion, trace, source and grade.
- These are ten single-rollout results, a selected example, and multiple harness
  edits together. They do not establish a component’s causal effect or a general
  success rate. Run 2’s cross-run determinism check failed despite greedy
  decoding; a fresh live attempt need not reproduce the same events.
- A pass means this task’s recorded tests passed, not exhaustive correctness.
  The saved counts are not recomputed by executing the submitted program.
- The live import is **not** this original executor: it inlines skill text,
  omits executable scripts/extensions, and starts with tools off. A disclosed
  live adaptation is a separate demonstration. This page provides no Apply,
  Run, Compile, Generate or Import action.

## Source boundary

The fixed source is `~/nomos_data/harness2-runs/mojo-glimmer-20261031`.
`src/lib/server/recorded-harness-config.ts` contains 37 per-file SHA-256 pins,
including the protocol/results notes, selected split, both aggregates, both
selected execution records, and both complete frozen harness inventories.
Captures and source texts remain on disk; none are committed or in the browser
bundle. The endpoint returns their inert presentation only after validation.

`GET /api/workbench-recorded` is same-origin loopback-only, no-store, accepts no
parameters and has no write method. The browser cannot select a path, root or
pin. On missing, changed or malformed evidence it returns a generic 404 and no
replacement data. Every consumed input is re-hashed on every request. Inputs
must remain operator-owned and quiescent; this is not a hostile concurrent
parent-directory mutation defence.

The common bounded no-follow reader validates paths/realpaths, regular files,
size and hash. Existing callers still refuse empty files. This adapter explicitly
allows empty harness files and stderr, since an empty advisory is valid evidence.
Limits are 4 MiB per trace, 1 MiB per other file and 16 MiB total. No recorded
code is dynamically imported, rendered as HTML, followed as a path or executed.

The adapter verifies:

- exact ordered ten-task roster and matching corpus identity;
- Python-compatible frozen harness digests (README stubs excluded by the
  executor’s original surface rule), alongside every individual file pin;
- launch/completion identity, all four completion output bindings, the selected
  aggregate grade, task/source hashes and grader runtime identity;
- actual boolean verdicts, compile/run status consistency and separate quality
  scoring; failed rollouts stay failed, with unknown reason explicit;
- unique model/tool call IDs, matching arguments and results, completed ordered
  exchanges, settled/non-aborted status and exact model/tool counters.

Raw trace fragments are not replayed. All final tool exchanges, their preceding
assistant messages, and the final answer are preserved. JSONL line ranges make
each displayed tool result traceable. Token/cost counters reported as zero by
this executor are not displayed as measured zero usage.

## Verification and review

Use Node 24.22.1. Build **before** the browser check. The five commands in README
cover fixture negatives, the real input recount, check/build, browser isolation
and existing-feature regressions. Browser tests use temporary databases and a
fake kernel solely to test navigation from an attached conversation. They never
contact :8091, :8092 or :8093. Real artifact tests are read-only.

Screenshots and local evidence go to `preview/checks/workbench-recorded-*`;
completion evidence is in `.unlazy/workbench-recorded/GATES.md`. Neither is
committed. Review and merge are separate from deployment; this branch does not
restart `glimmer-lab-live`.
