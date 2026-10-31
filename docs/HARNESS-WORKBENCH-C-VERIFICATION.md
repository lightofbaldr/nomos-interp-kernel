# Workbench C — fixed sandboxed Mojo build/run

Review delta from lab slice B `5730bbd` and agent slice B `0b67613`.
No merge, live Glimmer request, paid call, GPU work, service restart, kernel edit,
runtime installation or dependency-security update is part of this slice.

## Package and operator setup

The lab vendors agent **a84c710** as `vendor/glimmer-agent-a84c710.tgz`:

```
292616bbc1b3f51c928b83aa7cf1bbf3fd0541459891dc21d49d5c14ef2eda3d
```

A second `npm pack --ignore-scripts` into a fresh directory is byte-identical.
Both repositories use the local Light of Baldr identity, on review branches.
The agent policy advances to `glimmer.profile/tools-2`; old revision records are
not rewritten. File-only rendered instructions remain unchanged.

Set `GLIMMER_MOJO_PREFIX` to the operator-approved installed prefix, rebuild/run
the application, check the toolchain from Profile, then explicitly enable `mojo`.
It is off by default. No compiler is provisioned by the feature. Tested prefix:

```
/home/adam/Projects/nomos-interp-kernel/.pixi/envs/default
```

Selected identity hashes used by the real CPU tests:

| File | SHA-256 |
| --- | --- |
| bin/mojo | 0bce743c710ca91b9513eb6bbe600dcce41892dd2b52f29427b17eeeb97d32fa |
| bin/lld | f9d37839afe8decea7be4bf3e65e5de87f4a805f85017807f329644835a2d220 |
| lib/mojo/std.mojoc | 0ca4b40d6907d4fd631ffbf092aee2d4b7cc99baa63f277eea52d56a0b3f8d68 |
| share/max/modular.cfg | 83637d093cdd48818e1749d82a61222054579c8a3ff0422b18f1d39ac832dcf8 |

These are selected disk identities, not every installed shared library. The
readiness check runs the version command, not a full compiler health test.
Actual compilation is exercised by the tests below.

## Acceptance checks

Run from the respective repository; all providers in these checks are fixtures.

| Check | Command | Scope |
| --- | --- | --- |
| Agent | `npm test` plus typecheck/build/vendor check | 65 tests; fixed tool schema, explicit readiness, unsafe recovery, existing Pi contracts |
| Real Mojo sandbox | `node scripts/mojo-tests.mjs sandbox` | 7 tests, real compiler/native execution and negative controls |
| Pi integration | `node scripts/mojo-tests.mjs integration` | 4 tests, real Pi + fixture provider + actual broker/compiler |
| Full app regression | `node scripts/mojo-regression.mjs` | Fresh build/check, existing live/annotator/profile/file suites, real Mojo browser |

Final offline result: all three lab acceptance commands passed, with no skips;
agent 65/65, Mojo sandbox 7/7, Mojo integration 4/4, existing file sandbox 15/15,
and file integration 6/6. The full runner ended `MOJO REGRESSIONS PASS` after its
fresh build/check and every nested browser/regression gate. Local retained logs:
`/tmp/glimmer-c-agent-final.log`, `/tmp/glimmer-c-gates.log`,
`/tmp/glimmer-c-gates-verified.log`, `/tmp/glimmer-c-regression-verified.log`.
Gate-runner exit 1 at that point means only the manual branch/handoff gate was
still pending; the three executable gates were green. The local ledger records
the final handoff separately.

Retention follow-up after Prime's review: byte-identical copies of these logs,
the runtime/Undici follow-up logs and both original gate ledgers now live at
`.unlazy/workbench-c-live/prior-evidence/`, outside `/tmp` and ignored by git.
That directory's `SHA256SUMS` verifies all 15 retained files. Original `/tmp`
files were not removed. This changes their retention, not the historical results.

The browser test covers disabled-by-default controls, availability, saved profile,
write → compile → run, durable stdout, readable output contrast, a compile failure
with no run, and desktop/mobile overflow. Program text is escaped. Compile/run
output is shown first; the complete recorded result/evidence remains inspectable.
Screenshots are local ignored files under `preview/checks/mojo-{desktop,mobile}.png`.

Verification corrections: an initial full gate failure retained only its output
prefix; subsequent runs retain the entire runner log. The retained browser
failure was a faulty new oracle treating OKLCH values as RGB. It now uses the
existing canvas-to-sRGB method with black/white controls. A later heading-space
assertion exposed Svelte's whitespace trimming; the heading is now one explicit
string. The fresh build plus Mojo browser check passes with both corrections.

The sandbox tests include missing compiler, extra arguments, traversal/symlink
sources, invalid code, runtime nonzero exit, output flooding, infinite execution,
TERM cleanup, a deliberately failed isolation proof, and compiled code attempting
source/config writes, host-file reads and host-loopback access. Host canaries are
test-only. No test targets :8091. Resource assertions check the effective service
properties, including 4 GiB RAM, no swap, 128 tasks and the phase deadlines.

The Pi integration also runs from a foreign cwd containing a substitute broker;
it still selects the app's trusted runtime. That closes B's cwd-resolution note.
Missing Mojo does not prevent file-only profiles or text-only conversations.

## Boundary and residuals

See [the operator/boundary description](HARNESS-WORKBENCH.md#mojo-build--run).
The only argument is a workspace source path. No flags, program args, stdin,
environment or shell API are accepted. Both phases have read-only source mounts,
private disposable scratch, no host network/home/GPU, and independent cgroup
limits. Native code may spawn subprocesses **inside** those same restrictions.
This is not a correctness grader or proof against a malicious local operator.

The scratch watchdog is not a hard filesystem quota. SIGKILL can leave disposable
host scratch; service time bounds and next-admission orphan cleanup still apply.
The compiler/runtime installation must remain operator-controlled and quiescent.
No imported extension code is executed by the host. Unsafe recovery prevents an
automatic rerun of an interrupted Mojo invocation.

## Demo follow-ups

[Undici reachability and the subsequent repair](UNDICI-REACHABILITY-2026-10-30.md):
the initial inspection did **not** classify it as unreachable. The follow-up pins
the already-installed Node 24.22.1 / built-in Undici 7.30.1 and overrides the npm
copy to 6.29.1. The original results above remain historical; the new offline run
is recorded separately. No live process was restarted by either change.

Prime reviews this slice before any merge request to Adam. A live Glimmer E2E
requires a coordinated slot; none was used here. The offline fake-model/native
compiler path does not establish Glimmer's task-solving success rate.

The Svelte/AI Elements guidance kept the UI in existing local components; the
CSS-scoping guidance confined the output readability fix to tool results. Mojo
syntax was checked against the installed compiler, and the gate ledger separates
offline acceptance from the unrun live check.
