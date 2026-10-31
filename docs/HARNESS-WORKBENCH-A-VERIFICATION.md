# Slice A review handoff — 2026-10-30

Review branches: `codex/harness-workbench` in both private GitHub repositories.
No merge is requested without Adam's approval.

- **glimmer-agent:** `a287b8c..478f17a`. Native Pi configuration, rewindable profile
  document, serialized activation/submission, pending edits, fork/restore, and
  non-model per-request attribution. No tools or extension discovery.
- **glimmer-lab:** changes after design `c86e0af`. Operator API, bounded no-follow
  text importer, Profile editor, budget/delivery disclosures and historical badges.
- Vendored `glimmer-agent-478f17a.tgz` SHA-256:
  `75258824e31cbf33bb0ddd80b4693fd0e3fe317f02da64f92e82eb0240123717`.
  A second `npm pack --ignore-scripts` was byte-identical. Pi stays at 1.3.0.

## Checks

- Agent: **60/60**, typecheck, package exports and vendor pins pass.
- New lab CPU units: **3 profile integration + 5 import tests**, passing.
- Existing live/meta units: **40/40**, passing.
- Fresh `npm run verify`: zero errors/warnings, production build passes.
- Full existing offline regression, live bridge browser and fake Meta browser pass.
- Profile browser covers save, add/toggle/remove skills, held-run pending edits,
  next-submit activation, old-turn inspection, independent sessions/reconnect,
  inert extension preview, import, reload, restore/fork and mobile layout.
- Imports have negative controls for traversal, symlinks, parent replacement,
  hard links, changed sources, invalid UTF-8, budgets, duplicate skills,
  preview/version/session substitution and a host-write sentinel that never runs.

Two clone bugs were fixed during implementation: reactive UI drafts and Chord's
transactional provenance arrays are proxies, not structured-cloneable data. The
validators now produce canonical plain content; provenance is copied as data.
The import-edit provenance witness was observed failing before its fix.

**Browser timing note:** one final-ledger profile-browser invocation timed out at
a 12-second locator wait. The ledger runner retained only a shortened failure,
not its location. A direct rerun and three consecutive repeat runs passed. The
browser test now saves full failure diagnostics and a screenshot; the cause of
that single timeout is not established. Do not describe it as a proven fixed bug.

Screenshots: `preview/checks/profile-fake-desktop.png` and
`profile-fake-mobile.png` (local, not committed). Gate evidence is in
`.unlazy/harness-workbench-a/GATES.md` (local). Use the commands in
[the workbench guide](HARNESS-WORKBENCH.md) to reproduce the checks.

## Boundaries

All generation was against local synthetic HTTP producers, with isolated test
databases. No request to `:8091`, no GPU job, no paid call, no live bash rollout,
no service restart, and no read/write of the running Harness² job's artifacts.
No captures, secrets or build output are committed. Repo-local author/committer
identity is Light of Baldr throughout.

This is slice **(a)** only. Sandboxed file tools **(b)** and fixed-argv sandboxed
Mojo compile/run **(c)** remain next. Inline import deliberately differs from
Harness²'s on-demand skills; no exact executor-reproduction claim is made.
