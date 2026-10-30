# Harness² × Glimmer × Mojo

The new `mojo` benchmark uses the existing Harness² Propose–Probe–Compose method
and Pi 1.3.0 substrate. Glimmer executes the task; the existing `claude_cli` or
`codex_cli` channel edits the harness. Compile/run is the deterministic outcome.
Muse Wonder is an optional code-quality opinion beside that outcome, never its
replacement. Neither grade is fed back into the adaptation loop.

**Delivery status: offline only.** No live Glimmer rollout, improver session, or
paid Muse request was run for this implementation. Adam's approval, relayed by
Prime, is required before the first live step. The serve's tool-name fix belongs
to Prime; this runner does not strip or repair tool names. It never starts,
stops, or restarts the serve.

## Local setup and offline checks

Use Python 3.12, Pi 1.3.0, Node, Mojo 1.x, bubblewrap with user-namespace disabling,
and a working systemd user manager with cgroup-v2 resource controls. The tested
compiler is Mojo `1.0.0b2 (2cf4d08a)`. The new Python path uses only the standard
library. Install this repository into a dedicated virtual environment:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m unittest discover -s tests -p 'test_mojo*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_sandbox*.py'
.venv/bin/python -m unittest discover -s tests
```

The sandbox tests use the real installed Pi binary against an ephemeral local
fake HTTP/SSE model, not the running Glimmer serve. They also compile and run small
fixture programs in the real no-network sandbox. The judge tests use a temporary
fake key and fake HTTP API; they never load the real credential.

```bash
.venv/bin/harness2 --bench mojo --substrate pi --split train --k 1 --dry-run
HARNESS2_IMPROVER_MODEL=offline-preflight-only \
  .venv/bin/harness2-check --bench mojo --substrate pi
```

The first command only reads task sources and prints the selected IDs, hashes,
stream order and namespaces. The second is also offline: it tests the namespace,
tool versions, and a known-good compile/run control. The supplied model name is
not contacted. It is not a live readiness or credential test. Do not use the
inherited `harness2-setup jb --substrate pi` for this bench; Mojo does not need or
share its host-auth links or benchmark dependencies.

Defaults, overridable before creating a run:

| Setting | Default |
| --- | --- |
| `HARNESS2_MOJO_DATA` | `~/Projects/nomos-mojo-eval` |
| `HARNESS2_MOJO_PREFIX` | `~/.pixi/envs/mojo` |
| `HARNESS2_MOJO_PI_PACKAGE` | Package containing the resolved installed `pi` bundle |
| `HARNESS2_MOJO_SEED` | `20261030` |
| `HARNESS2_MOJO_TRAIN_COUNT` / `_TEST_COUNT` / `_SMOKE_COUNT` | `4` / `10` / `3` |
| `HARNESS2_RUNS_DIR` | Repository-local `runs/` (ignored by Git) |

Node is resolved from `PATH`. Pi must be the bundled 1.3.0 installation. The
compiler prefix must contain its `bin`, `lib`, `include` and `share/max/modular.cfg`.
The sandbox relocates that configuration without sourcing activation scripts.
No toolchain or task data is downloaded by the Mojo commands.

## Task selection and what the oracle means

The default corpus is frozen from these read-only source files:

- `tasks_hard.jsonl`: draw 4 training tasks from 28 eligible candidates.
- `heldout_eval.jsonl`: draw 10 heldout tasks from 21 candidates.
- `tasks.jsonl`: draw 3 runnable smoke tasks from 12 eligible candidates.

Selections use `random.Random("harness2-mojo-v1:{seed}:{split}").sample` over
sorted IDs, independently per split. The full source-file hashes, seed,
exclusions, exact selected IDs and normalized task hashes enter the run identity.
A `--split-file` may instead explicitly select nonempty `train` and `test` ID
lists. It cannot move tasks across the source partitions; it does not select a
smoke subset. Changing the corpus or harness requires a fresh run namespace.

Prime's pre-run rulings are applied without editing the sources:

- Exclude `simd_dot`: the two written dot products are both 20, not the stored 30.
- Keep `generics-first-mismatch` and `generics-median-of-three` in heldout;
  exclude their training copies before sampling.
- Reject other duplicate IDs/prompts. These checks establish exact identity
  separation, **not semantic independence** between related problems.

Complete-program tasks compile and, for `kind: run`, execute with expected
stdout. Function-completion tasks append the source dataset's host-owned test
main in a **new grading sandbox**, then require exit zero and stdout `ok`.
The rollout, improver and Muse payload receive no test main or reference answer.
Stdout comparison strips leading/trailing whitespace and preserves internal
whitespace. Empty delivered code is an observed failure. A known-good compiler
control failing is unscored, not zero; it is cached only for the same recorded
runtime identity.

This is the dataset's compile/stdout oracle, **not a tamper-proof evaluator**.
The compiled candidate can inspect its own grading source or intentionally
spoof stdout. Passing these fixtures does not establish general correctness or
resistance to grader evasion. No competence/prevention claim is made.

## After authorization: adaptation, frozen evaluation, reporting

These commands describe the future run, not permission to launch it. First
freeze an explicit improver model and channel, a fresh `HARNESS2_RUNS_DIR`, and
confirm Prime's serve fix. Do not set a live flag until approval arrives.

1. `harness2-mojo base` writes the compact immutable starting harness and prints
   its path. `base --dry-run` only prints the path.
2. `harness2-mojo evaluate --split smoke --harness <base-path>` runs the selected
   smoke tasks, with no adaptation.
3. `harness2 --bench mojo --substrate pi --split train --k 1 --modes parallel`
   uses the existing recursion. At k=1 the offline method test exercises four
   executor rollouts. Use `HARNESS2_IMPROVER_CHANNEL=claude_cli` or `codex_cli`
   and explicitly set `HARNESS2_IMPROVER_MODEL`. Always-on text is capped at
   6,000 characters; the entire editable harness at 80,000. Subagents are disabled.
4. Select and freeze an adopted training harness from the task summary's
   `selections` / `chosen_label`, under that task's `harnesses/` directory. Then
   run `harness2-mojo evaluate --split test --harness <base-path>` and the same
   command with the frozen improved harness. Both use the identical ten-task
   heldout selection. This CLI never proposes, selects or reflects on heldout.
5. `harness2-mojo report` shows deterministic outcomes and Muse opinions in
   separate fields, including ungraded/unscored completed runs as unknown.
   `harness2-judge --bench mojo --substrate pi --k 1` also plugs into the existing
   adaptation reports; do not use its feedback to choose a heldout harness.

Executor admission requires `HARNESS2_MOJO_LIVE_APPROVED=1`. Optional judging
separately requires `HARNESS2_MUSE_APPROVED=1` and an explicit positive
`HARNESS2_MUSE_MAX_CALLS` (maximum 100). The default is **no paid calls**. Each
run-root's SQLite ledger fixes the cap, reserves a slot before transmission,
counts failed requests, and caches exact duplicate payloads. Pending calls are
not retried after a crash. Do not create new ledgers to evade an approved budget.
The flags are operator acknowledgements, not an authorization service.

Muse uses only `META_API_HACKATHON` read from `~/.env` on the host. No fallback to
the historical misspelling or inherited environment. The key never enters Pi,
payloads, logs or reports. Input leakage and response echoes are refused. The
fixed HTTPS endpoint has no redirects, retries, proxy-environment support or
tools. A strict JSON rubric gives 0–4 for readability, idiomatic Mojo and
robustness. Invalid, absent and budget-limited opinions have null scores.

## Sandbox boundary

The implementation follows bubblewrap's [namespace/mount mechanisms](https://github.com/containers/bubblewrap/blob/main/bwrap.xml),
not a prompt-based command allowlist:

- Pi, its tools/extensions, the compiler and submitted programs run inside
  bubblewrap, with a private PID/network namespace, no capabilities and further
  user namespaces disabled. Only the per-run `/work` tree is persistent writable
  storage; `/tmp`, home and caches point inside it. `/usr` and the required Node,
  Pi and compiler paths are read-only. Host home, `/etc` credentials, user bus,
  host process namespace and GPU devices are not mounted.
- The fresh Pi config contains no real API key, host auth, host skills, packages
  or extensions. Explicitly rendered harness skills/extensions remain inside the
  sandbox. Pi's stdin is closed, auto-discovery/retries/compaction disabled, and
  sampling is fixed to temperature zero.
- Isolated loopback is not host loopback. A private Unix-socket broker supplies
  only `POST /v1/chat/completions` to host `127.0.0.1:8091`, for the fixed Glimmer
  model, with at most 32 requests per rollout. It rejects `nomos` controls, other
  routes/models, missing or excessive output-token bounds, and forwarding of
  credentials. No general CONNECT proxy or external network. Closing the
  rollout revokes queued admission and closes active upstream connections.
- Compile/run has **no broker at all**. Expected outputs remain host-side.
- Each process tree is a separate systemd user service: memory 4 GiB, no swap,
  128 tasks, two CPUs' quota, 64 MiB per file, 256 open descriptors, and a wall
  limit. The entire service is stopped on exit, timeout or output overflow.
  A bounded, non-following workspace scan stops at 256 MiB, 8,192 entries or
  excessive nesting. It is a watchdog, **not a hard filesystem quota**.
- One file lock serializes rollouts sharing a run root. Coordinate separate run
  roots/other serve clients with Prime; this is not a global GPU reservation.

There is no claim of protection against kernel vulnerabilities, a hostile host
writer, or every resource-pressure failure. Bubblewrap is a mechanism whose
security depends on the arguments and host; see its [security scope](https://github.com/containers/bubblewrap#sandbox-security).
The inherited trusted improver CLI remains outside this new executor sandbox.
Legacy JobBench/LAB/WorkBuddy launchers are unchanged and must not be described
as sandboxed by this work.

## Evidence and resume

Each rollout records its task/harness digest, fixed executor/sampling settings,
selected runtime-file hashes, arguments, call count, process outcome and Pi
events. Completion binds the exact harvested output set. Resume rechecks hashes
and refuses partial/changed output; failed attempts are preserved, not retried.
Compiler identity/control and Muse payload digest/rubric accompany their results.
This records selected runtime files, **not a full hermetic dependency closure**.

Keep datasets, outputs, credentials, caches and personal run artifacts out of
Git. The source checkout and its Forgejo remote are untouched; see
[the fresh snapshot provenance](SNAPSHOT-IMPORT.md). Before public export, scrub
the workstation-specific source path in `scripts/check_snapshot.py` and local
provenance paths; preserve licenses and third-party notices.
