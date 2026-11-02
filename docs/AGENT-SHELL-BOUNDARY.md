# Operator-disabled sandboxed shell

This separate commit adds a trusted shell broker and an optional Pi catalog
entry. It does not enable a profile or grant the model host execution. The
application keeps shell off by default behind an explicit server-operator switch
and an opt-in profile capability. No missing-tool, failed-probe, timeout or cleanup path
falls back to a host shell.

The integration wires the fixed `bash` catalog entry through
this broker. Only exact `GLIMMER_ENABLE_SANDBOX_SHELL=1` enables the server-side
path; the flag is absent by default and must remain absent pending Adam's
decision. The browser cannot change it. Even with the flag, a profile must
explicitly select bash after its probe passes. Both readiness and execution
refuse when disabled. The normal Agent-mode switch adds only file/Mojo tools.
No live deployment or live shell enablement is authorized by this commit.

Bounded helper import is deferred under Prime 1716: the existing profile schema
stores inline skill text, not helper bytes. An imported `check_mojo.py` is not
materialized by this patch. The sandbox's optional compiler does appear on PATH
and at `/opt/mojo/bin/mojo`; a manually supplied helper executes only there.
Extensions are never loaded (including `mojo-verify-note.ts`). The UI discloses
these gaps rather than claiming unmodified Harness² execution.

## Callable interface

The server invokes fixed argv with `shell: false` and a minimal environment:

```text
/usr/bin/python3 -I -B <app>/runtime/files/shell_broker.py <private-root> <conversation-id|probe> [<operator-owned-mojo-prefix>]
```

Only `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` from the operator environment
are needed for the local user service manager; host subprocesses otherwise use
`PATH=/usr/bin:/bin` and `LANG=C.UTF-8`. They are not passed into the sandbox.
The application, not the model, chooses every CLI argument and executable path.
The broker imports only its trusted sibling runtime files; never workspace code.

One JSON object is sent on stdin, followed by EOF:

```json
{"command":"python3 check_mojo.py"}
```

The exact sole field is `command`: nonempty UTF-8, at most 16 KiB, without NUL.
Duplicate JSON keys, other fields and requests over 128 KiB are refused. There
are no request-controlled cwd, environment, mounts, limits, flags or host argv.
`probe` accepts only `{}` and runs trusted bash-version checking, plus
`mojo --version` when that operator-owned compiler is configured. A probe is an
availability check, not permission to run a model command.

Inside isolation, the trusted entry ultimately execs
`/usr/bin/bash --noprofile --norc -c <command>` in `/work`. Bash parses the command
only there. Command stdin is `/dev/null`; there is no interactive session or
host input channel. Workspace Python helpers execute inside the sandbox and are
not host extensions. Commands can mutate/delete workspace data, and interrupted
mutations are not rolled back or automatically retried.

A valid tool response exits the broker with status 0, even when the command
fails. Its JSON is:

```text
{ok, policy: "glimmer.shell/bwrap-1", unit, result: {
  exit_code, reason, stdout, stderr, captured_bytes, seconds, probe,
  command_sha256, evidence: {isolation, resources}, workspace?, compiler,
  transport_truncated?
}}
```

`ok` requires zero service exit and no limit reason. `exit_code` is the
systemd-run process status, not a claim about a particular signal. Reasons are
`timeout`, `output_limit`, `workspace_limit` or `transport_limit`; a normal
nonzero command exit has `reason: null`. Kernel/systemd resource termination may
also appear as a nonzero exit with no more specific reason. Output is untrusted
text, never interpreted as protocol, success, HTML, or source provenance.
`command_sha256` identifies command text, not all helper bytes or correctness.
`captured_bytes` counts retained raw stream bytes before UTF-8 replacement and
possible JSON-transport truncation.

Admission/infrastructure/cleanup failures exit 3 with
`{ok:false, unavailable:true, policy, error}`. The application must reject those
as unavailable and must not replay mutations. Allow enough outer time for the
shared lock wait (35 seconds), 90-second execution, and bounded service cleanup;
the current Mojo broker's 160-second outer watchdog is a suitable integration
budget. Abort should TERM, join cleanup, and only hard-kill/poison the broker on
a separate cleanup deadline, as in the existing application broker.

## Mount, credential and process boundary

The existing `broker.locked_workspace` lock, owner-private root checks and exact
service name are reused across file, Mojo and shell operations. They serialize
the same conversation and settle an orphan service before a new command is
released. The source directory is checked against its held descriptor before
launch. Parent/root directories are not mounted in the command namespace.

Bubblewrap starts with all supported namespaces unshared, further user
namespaces disabled, a new session, no capabilities, no-new-privileges, and a
cleared environment. The host confirms that net, PID, mount, user, IPC and UTS
namespace IDs differ from its own. Before releasing command JSON it checks the
trusted entry's complete readiness report and independently reads back the
effective systemd resource properties. The service owns the entire descendant
cgroup; cleanup calls the existing stop-and-confirm routine before returning a
result or releasing the workspace lock. Cancellation is deferred through
launcher creation, then handled before command admission; cleanup first joins
the launcher so it cannot create a late service after settlement.

| Path | Access and source |
| --- | --- |
| `/work` | Only persistent writable host bind; this conversation's workspace |
| `/tmp` | New 256 MiB private tmpfs per call; discarded afterward |
| `/usr`, `/bin`, `/lib`, `/lib64` | Host installed runtime read-only, with fixed compatibility symlinks |
| `/runtime/shell_entry.py` | Trusted entry file, read-only |
| `/home`, `/run`, `/etc` | Empty read-only directories; `/etc/alternatives` gets only validated linker symlinks for optional Mojo |
| `/proc`, `/dev` | Private namespace proc/minimal devices; top-level mounts read-only, no host GPU devices |
| `/opt/mojo/{bin,lib,include}` | Optional operator-selected compiler directories, read-only |
| `/config` | Optional read-only adapted compiler config; cache/crash paths point to private `/tmp` |

There is no host home, credential directory, host `/tmp`, session database,
host runtime socket, GPU device or network namespace mount. Descriptor
inheritance into the sandbox is restricted to its standard pipes; the entry
replaces stdin with `/dev/null`. The command environment contains fixed PATH,
LANG, HOME, TMPDIR, XDG_CACHE_HOME and PWD; optional Mojo adds MODULAR_HOME.
Python may add LC_CTYPE and bash adds its own PWD/SHLVL/`_` bookkeeping. No
API-key, proxy, BASH_ENV, PYTHONPATH or general host environment is inherited.

Optional compiler setup reuses `mojo_broker.toolchain`: the same identity-file
hashes and config rewriting, with before/after identity comparison. It does not
mount the whole prefix, repository, host cache, or kernel tree. These hashes
cover four identity files, not every transitive compiler/runtime file; the
operator owns and must keep the installation quiescent.

## Fixed limits

| Control | Limit |
| --- | --- |
| Host execution watchdog | 90 seconds, including service startup |
| Independent service runtime | 92 seconds |
| CPU | 200% cgroup quota; 90-second hard per-process RLIMIT_CPU |
| Memory / swap | 4 GiB cgroup memory; zero swap |
| Tasks / open files | 128 cgroup tasks; 256 descriptors per process |
| File size / core dump | 64 MiB per file; core dumps disabled |
| Stdout / stderr | 16 KiB raw bytes each; excess stops the service |
| Final JSON | At most 60,000 bytes plus newline; further truncation is explicit and fails `ok` |
| Workspace guard | 256 MiB logical file bytes, 8,192 entries, depth 32 |
| Private `/tmp` | 256 MiB tmpfs, also charged to cgroup memory |

The workspace guard is a bounded no-follow metadata walk before admission,
every 250 ms while running, and after settled cleanup. It rejects symlinks,
multi-link regular files and special files, preventing preexisting host-linked
inodes from becoming command input. Commands that leave such entries make the
workspace unavailable until repaired. The guard is **not a disk quota**: a fast
writer can overshoot the aggregate byte/entry limits between scans. LimitFSIZE
does not impose a total-workspace cap. Interrupted or rejected commands can
leave files, changed permissions or an over-budget workspace. No host actor may
concurrently replace the private ancestor paths or inject links into the tree.
Existing file tools keep their stricter path/text conventions: shell-created
binary or large files may not be text-readable there, and unusual filenames can
make file operations refuse until repaired. Shell success is not a guarantee of
compatibility with every other workspace viewer or tool.

## Verification and scope of evidence

Run with the existing Node 24.22.1 and Linux user systemd/bubblewrap prerequisites:

```sh
node scripts/shell-tests.mjs
```

These are real namespace/service executions, not a mocked bwrap backend. The
suite checks admission/read-back, fail-closed missing executor/isolation/limits,
separate workspaces and scratch, fixed stdin, JSON validation, hardlink/symlink/
FIFO refusal, readonly/path/device restrictions, disabled nested user namespaces,
shared file-tool locking, output/transport limits and forged-result resistance.

All credential probes create temporary **decoy** `.env`, SSH-key, outside-file,
environment and passed-descriptor canaries. The same detector must fail against
positive host controls before absence inside the sandbox counts as evidence.
The network control similarly proves a test-owned loopback socket reachable on
the host and unreachable inside. No real `~/.env`, keys or credentials are read.

Resource tests exercise the real 128-task fork ceiling and 64 MiB per-file bound.
The default limits are measured through actual cgroup/readiness evidence. To
avoid pressuring the laptop or waiting 90 seconds, trusted copied fixtures
lower memory to 64 MiB, CPU to one second, tmpfs to 1 MiB, the host watchdog to
one second and the independent service watchdog to two seconds for their
respective enforcement tests. Those tests demonstrate the mechanisms under
lower bounds; they do not time a full 90-second production command. TERM,
SIGKILL-orphan recovery and detached-child late-write canaries check teardown.
A held-launcher control also aborts between process creation and launcher-handle
assignment, checking that cancellation cannot leave an admitted command behind.

The optional compiler control uses only the existing
`tests/mojo/fixtures/hello.mojo`, a small synthetic `check_mojo.py`, and the
existing local compiler fixture prefix. It builds/runs CPU hello inside this
sandbox and checks compiler/config read-only access. It does not run a model,
live session, GPU workload, kernel build, or the improved harness itself. This
is not full Harness2/check_mojo behavior or correctness-grader parity.

Residual trust: this shares the host kernel and installed `/usr`; there is no
seccomp syscall allowlist, VM boundary, hostile-same-UID-host-writer defense or
hard aggregate disk quota. An operator must keep secrets out of explicitly
mounted runtime/toolchain trees. Private proc/dev kernel interfaces remain
available, though the host's proc/dev trees are not mounted. A broker hard-kill
cannot run its finally block; the independent service deadline bounds the
orphan and the next locked operation stops it before reuse. If cleanup cannot
be confirmed the broker fails unavailable; application integration must remain
fail-closed. None of these controls warrant enabling shell by default.
