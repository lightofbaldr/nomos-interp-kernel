# Harness workbench slice B — review handoff

2026-10-30. Branches only; no merge. Slice C (Mojo build/run) is not part of this
change. No request to the live `:8091` endpoint, paid API, GPU job or service
restart was made. Models in the tests are local synthetic HTTP producers.

## Source and package

- glimmer-agent: `478f17a..0b67613`, `codex/harness-workbench`.
- glimmer-lab: changes after slice A's `be07fbe`, same branch name.
- Vendored `glimmer-agent-0b67613.tgz`:
  `0fdd55a2a84ef2cec5ebef7242cd51e10a56f202ffc3abbaa39be46f4a18c58f`.
  Two independently invoked `npm pack --ignore-scripts` runs were byte-identical.
- Pi packages stay at 1.3.0. No third-party dependency version changed.
- Commits use the repo-local Light of Baldr identity. No workspace data, SQLite
  database, screenshot, build output or key is part of the source change.

## Delivered

Six fixed tools (`read`, `write`, `edit`, `ls`, `find`, `grep`), off by default;
one private workspace per conversation; availability checks before activation;
native Pi registrations and durable calls/results; pending changes wait through
all tool rounds; recorded tool settings in the profile inspector. Empty imported
files are marked **omitted (empty)**, including empty advisory files, and add no
prompt headings. Imports do not enable tools.

Every model-requested filesystem operation runs inside the fixed bwrap worker.
The trusted host broker launches it, checks namespaces and effective cgroup
limits before releasing the request, and joins cleanup. It never evaluates model
code, interpolates a command or falls back to host file operations. The UI uses
the existing Svelte AI Elements tool presentation. Skills remain explicitly
inline; no on-demand filesystem skill delivery is claimed.

## Verification

Run the final composite check from the repository root:

```sh
node scripts/filetool-tests.mjs integration
node scripts/filetool-tests.mjs sandbox
node scripts/filetool-regression.mjs
```

- Agent: **63/63**, plus typecheck, build, package exports and vendor pins.
- Lab integration: **6/6**. Real Pi and real sandbox with a fake model; native
  tool configuration and request attribution, SQLite reopen, independent fork,
  restore without filesystem rewind, held-tool multi-round profile changes,
  unavailable broker, shutdown and an actual process crash after a file effect.
  Explicit Pi recovery reports the unsafe write as interrupted; it is not
  repeated. Merely opening storage is not a recovery operation.
- Sandbox: **15/15**. Real transient services: six operations, distinct workspace
  roots, traversal/link/special-file refusals, symlink churn, UTF-8/paging/bounds,
  edit ambiguity, concurrent serialization, abandoned atomic staging cleanup,
  isolation failure with no effect, effective-limit mismatch, cancellation,
  private directory checks, memory cap and the real 30-second runtime limit.
  The resource-limit fixtures run only inside test-owned constrained services.
- Fresh production build and `svelte-check`: **0 errors / 0 warnings**.
- Full offline application regression, including profile tests **3/3**, import
  tests **6/6**, existing live/meta/steering/contract/integration/start checks,
  and both profile and file-tool browser flows. Browser checks use fake endpoints
  and real local file isolation, at desktop and mobile sizes.

The working acceptance ledger is `.unlazy/harness-workbench-b/GATES.md` (local,
uncommitted); the automated gates are re-run after the last code edit. Images
are under ignored `preview/checks/`. Passing these tests is evidence for the
specified local boundary, not a general-purpose sandbox certification.

## Limits and operator notes

- Linux bwrap, a working user systemd manager and the reviewed Python runtime are
  required. The entire `runtime/files/` directory must ship with the server,
  started from the repository root. A failed check means tools unavailable.
- This remains a trusted local-operator app, not remote multi-user isolation or
  authentication. Same-UID hostile host writers are outside the boundary; keep
  workspace parents quiescent. The source bind path is checked against the held
  directory descriptor. A `/proc/<broker>/fd` bind alternative was not used:
  this host's ptrace/Yama policy prevents the sibling service from reading it.
- Limits: 1 MiB/file, 256 MiB and 8,192 entries per workspace, depth 32; 64 KiB
  result; 256 MiB RAM, no swap, 16 tasks and 30 seconds per worker. Workspace
  limits are checked inventories, not filesystem quotas.
- SIGKILL cannot run Node cleanup. The independent service deadline and the
  next-operation orphan cleanup bound that residual. Interrupted mutations can
  have committed; Pi does not automatically replay writes/edits. Forks are empty;
  restoring a profile never rewinds files. Parent directories may remain after
  a failed write. Reserved atomic temporaries are safely discarded, not replayed.
- No full model rollout against Glimmer has been attempted while the live-run
  reservation is in force. A future live check needs Prime's clearance.
- `npm audit` reports 19 low and one high vulnerability in the installed graph.
  The high finding is transitive Undici 6.29.0. It was already present in the
  approved slice-A lockfile: a lockfile comparison found only the local agent
  package changed. No automatic dependency fix or claim of applicability was
  made; handle the pinned dependency update separately.
