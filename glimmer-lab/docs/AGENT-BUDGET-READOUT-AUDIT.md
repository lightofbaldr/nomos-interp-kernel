# Agent budget and readout audit

2026-11-01. Baseline inspected: f60d95d; implementation includes the reviewed
sampling relocation at e0e691c. Pi 1.3.0 and its existing broker are retained.

## Approved envelope

Prime 1712 superseded the preliminary audit proposal. The implemented choices
are Chat (6,144 requested output tokens per model request) and Agent:

| Bound | Agent preset |
| --- | --- |
| Requested output per model request | 8,192 tokens |
| Model requests per user run | 32 |
| Tool invocations per user run | 64 |
| Entire user run, including tools | 20 minutes |

There is no claimed measured-token/spend limit. A tool follow-up is a new Pi
GenerationTask but remains in the same admitted run. Counts debit before work,
including failed calls. Reconnect, restoration and task recovery cannot refund
or rearm an allowance. Recovery without its in-memory owner fails closed and
requires explicit Stop/settlement. Retry is an explicit new user action with a
new run, not automatic recovery. No mutation is automatically repeated.

The server owns an abort signal throughout the response body and tool work.
The existing SDK 10-minute timeout ends when fetch returns headers; it is not
the Agent run clock. Budget hooks precede readout admission. Stop preserves its
cause before joining Pi, tools and readouts; shutdown joins settlement before
closing storage. The synthetic controls include a stream which sends headers
then stalls, an in-flight tool, request 33, tool 65, and a stale timer.

Metadata entries have no model messages or edits. They bind the run, request
task/attempt/cutoff, settings version, counters, limits and stop cause. Broker
invocation UUIDs are not misrepresented as Pi tool-call IDs.

## Context and effective reply limit

The provider advertises 32,768 context tokens. That is client configuration,
not an attestation of the current serve. The serve clamps requested output to
`MAX_SEQ - prompt_tokens - 1`; it does not return that effective limit. No
automatic compaction is enabled.

Each new generation attribution records the advertised capacity and requested
limit. The UI calls the cap **requested**. A complete matching readout summary
can supply the prompt boundary; using that plus the recorded advertised
capacity, the UI derives and flags a smaller ceiling. This is explicitly a
derived value, not a returned serve field. Missing, incomplete, unpaired or
readout-disabled evidence is unknown. It must not be presented as proof that a
capped response reached the requested 8,192 tokens.

Prime 1714 separately verified the laptop engine and serve both use 32,768 and
chunked prefill. This is provenance for the authorized test, not a permanent
runtime guarantee. `agent-prompt-count.py` is measurement-only: pinned local
tokenizer, template, protocol and library versions; exact outgoing payload;
no serve import, accelerator backend or network. It requires prompt + requested
output + 2,048 reserve ≤ 32,768 and rechecks consumed bytes before returning.
Production does not run this Python counter or claim an exact preflight.

## Opt-in measurement

`scripts/agent-budget-e2e.mjs --fixture|--live NEW_DIR COUNTER_MANIFEST` uses two
fresh isolated Pi conversations, sequentially, with the same short write/Mojo
task, profile, greedy decoding and readouts. The first uses Chat's 6,144 cap;
the second uses the approved Agent envelope. The task is a tool-path/budget
smoke, not a repetition of the long generics rehearsal, a speed benchmark, or
evidence that raising a cap improves coding success.

Live mode is explicit, fixed to laptop :8091, and requires a coordinated slot.
It refuses established clients before starting and before each forwarded
request, counts the exact body offline, retains request/response measurements
and saved Pi records, and stops on the first failure without retry. It cannot
start/restart/reconfigure the serve or call a paid provider. The test has fresh
DB/workspace paths and does not read or mutate the live lab's conversations.
Report actual outcomes separately; running the fixture is not a live pass.

## Readout continuity

The real-Pi synthetic producer checks model → write → model → Mojo → model.
Distinct request/task/response IDs own their original emotion/workspace rows;
tool results, not readouts, enter the model context. Reopening the bridge does
not duplicate rows. A missing terminal summary remains `contract_error`, never
completed by the next request.

Follow mode retains the most recent generated row through a prompt-only next
request, with that row's actual request identity. Explicit selections do not
borrow. Tool-only responses have a Tool channel on the Workspace page and are
inspectable even when incomplete. Chat keeps the emotion dock and an optional
workspace pane visible while tool tasks execute. Readouts are not a correctness
grade and do not establish an emotional experience.

## Harness delivery boundary

The current importer snapshots instructions, enabled inline SKILL.md files and
advisory text. It does not materialize scripts, on-demand skills or extensions.
Restores include saved tool selection; forks have empty workspaces. Those
differences remain visible in Agent mode. A shell alone cannot make an imported
Harness² profile run unmodified.

Prime 1716 permits a later bounded script-only import with pinned per-file
provenance and dual `.pi/skills/.../scripts/` / `skills/.../scripts/` placement,
but explicitly allows deferral rather than rushing profile-schema/versioning.
That delivery change is deferred here. Extensions remain excluded; for the
improved rehearsal profile `mojo-verify-note.ts` is not loaded. The optional
shell has a separate operator switch, separate profile opt-in and separate
review commit; Agent mode never enables it. Its compiler is on PATH and at
`/opt/mojo/bin/mojo`, but full check_mojo/Harness² parity is not claimed.
