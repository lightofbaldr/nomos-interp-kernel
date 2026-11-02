# Harness workbench — slices A–C

The **Profile** tab beside Controls manages each Pi conversation's instructions,
ordered Markdown skills, imported advisory text and optional sandboxed tools.
The `mojo` tool builds/runs a workspace source with fixed argv. No model-supplied
command string, compiler flags or host extension is accepted by `mojo`.
The separate [shell option](AGENT-SHELL-BOUNDARY.md) remains operator-disabled
by default and held for Adam's decision; the ordinary Agent switch never adds
it. That option executes command text only inside its own checked namespace.

## Editing and history

- Save while idle applies a new revision. Save while a model/tool run is active
  creates a pending revision; it applies before the next user submission, after
  the whole run settles. Switching sessions loads that session's saved state.
- Unsaved edits survive same-session reconnects. Switching sessions or choosing
  **Reload saved profile** discards the unsaved form. A stale version cannot
  overwrite someone else's edit; reload and reconcile it explicitly.
- Enabled skills are **inline**, in full, before native Pi instructions. Disabled
  skills are not offered in the current prompt. Removal does not erase history.
  There is no home-directory or `.pi` discovery and no dynamic plugin loader.
- Every request gets a non-model `glimmer.profile.used` entry with revision,
  digest, policy, task/attempt/cutoff, effective profile and readout request ID
  when one exists. The assistant's profile badge inspects that recorded revision,
  never today's profile. Earlier turns without a record say **profile unrecorded**.
- **Restore** makes a new revision of the applied profile at that history entry.
  **Fork** uses Pi's native as-of history and starts with the applied profile,
  clearing pending edits. Neither operation rewinds any filesystem.

The default budget is **6,000 rendered characters**. A larger profile requires an
explicit checkbox, with a hard **64 KiB UTF-8 rendered-text** ceiling. Stored text
is also bounded (128 KiB JSON); there are at most 16 skills and 16 advisory sections,
each skill/section at most 16 KiB. The displayed character/4 token estimate is not
tokenization, total context usage or remaining capacity. Glimmer's configured
context is 32,768 tokens; conversation, template and output need room too.

## SKILL.md subset

Add pasted Markdown or select a local Markdown file. Frontmatter must contain a
unique `name` (1–64 letters/digits/underscore/hyphen, beginning with a letter/digit)
and a nonempty string `description`, followed by a nonempty body. Quoted strings
and folded/literal multiline descriptions are supported. Other YAML metadata,
tags, aliases and collections are refused, not interpreted. Inline skill content
is prompt text, not executable policy.

## Importing a Harness² text snapshot

Imports are unavailable until the operator configures a JSON array of approved
directories in **`GLIMMER_HARNESS_IMPORT_ROOTS`**, for example:

```sh
GLIMMER_HARNESS_IMPORT_ROOTS='["/srv/reviewed-harnesses"]'
```

Choose a root and a plain relative directory. The source is read only. No symlink,
hard-linked file, special file, traversal or arbitrary host-file picker is allowed.
The Linux reader anchors traversal to no-follow directory descriptors and checks a
second inventory/content pass. Limits: 256 tree entries, depth 6, 256 KiB per file,
2 MiB total and eight extension previews. This is for a single harness directory,
not a whole run/repository. Unexpected source changes refuse the preview.

Preview and confirm separately:

| Source | Effect |
| --- | --- |
| `systemprompt.md` | Native Pi instructions |
| `skills/*/SKILL.md` | Enabled inline skills, editable after import |
| `guardrails.md`, `tool_descriptions/*.md` | Named advisory prompt sections, not enforced rules or tool schemas |
| `extensions/*.ts` | Escaped code preview only; never loaded or executed |
| Other files, including scripts/resources/memory | Listed as omitted and not active |

The comparison shows both complete rendered texts. Its reference is explicitly
**Current profile**, or a separately selected **Base harness snapshot**. Applying
uses the server's immutable preview, bound to the conversation and saved version;
the client cannot substitute provenance or prompt contents. Previews expire after
15 minutes; at most eight are retained. Later source edits do not change a preview.
The applied revision retains its source path, file hashes, snapshot digest, delivery
warning and diff reference; later manual edits mark it as edited since import.

**Delivery matters:** Harness² listed skills for Glimmer to read on demand. This
slice inlines them. Inlining can hide a failure to read the skill, so an imported
base profile is not a faithful reproduction of Harness²'s base executor. Tools are
off; imported instructions that reference scripts, files or tools may be unusable.
Empty/whitespace-only prompt, advisory, skill and extension files are listed as
**omitted (empty)** and contribute no prompt headings. Applying an import turns
tools off; enable the required fixed-catalog tools explicitly afterwards. Inline
skills are not workspace files; on-demand delivery remains a future option.

## Sandboxed file tools

Click **Check tool isolation**, then select `read`, `write`, `edit`, `ls`, `find`
and/or `grep` and save the profile. All are off by default. Each operation uses a
new Linux bwrap worker in a transient user **service** with its own cgroup, not
the terminal's scope. Missing bwrap/user services or failed checks mean **tools
unavailable**; there is no host fallback. Text-only profiles still work. Isolation
is checked on enabling tools, before a tool-enabled submission, and inside every
worker before it receives the model's request. Saved tool settings survive restart;
availability is checked again rather than inherited.

The operator's session DB path determines its private root:
`<absolute GLIMMER_SESSION_DB>.workspaces/workspaces/<conversation-id>` (0700).
Only that conversation directory is mounted as `/work`; parent directories,
other conversations, home, host sockets, and the service manager's bus are absent.
The system runtime is read-only. Network/PID/user namespaces are isolated,
capabilities dropped and new user namespaces disabled. The broker uses fixed
argv and a small explicit environment; model input is JSON on stdin. No shell
interpolation or workspace code loading occurs. Keep `runtime/files/` with the
application. Broker paths derive from the source module in direct TS execution,
or Vite's fixed build root in production, never the server's working directory.
The resolved file broker path is logged at first runtime creation. Rebuild after
relocating the application; a copied build does not discover runtime code in cwd.

| Tool | Semantics |
| --- | --- |
| read | UTF-8 text, character offset, up to 12,000 characters per page |
| write | Atomic create/replace, creating parent directories within bounds |
| edit | Exactly one literal old-text occurrence; ambiguous edits refuse |
| ls | One directory, at most 200 entries |
| find | Recursive literal name substring (not regex/glob), at most 200 entries |
| grep | Recursive literal text, at most 200 matching lines clipped to 240 characters |

Paths are plain relative paths or `/work/...`, with `.` for a directory root.
Descriptor-relative/no-follow operations reject traversal, symlinks, hardlinks,
special files and reserved `.glimmer-` names. Files are at most 1 MiB; results at
most 64 KiB. Workspace checks enforce 256 MiB/8,192 entries/depth 32. This is a
bounded inventory and mutation check, **not a hard filesystem quota**. The worker
has effective limits of 256 MiB RAM, no swap, 16 tasks, 100% CPU, 1 MiB file size,
128 descriptors and 30 seconds. The broker reads those service properties back
before releasing the request. A contended workspace lock waits at most 35 seconds;
the outer deadline and mandatory cleanup have separate bounded allowances.

All operations serialize per workspace with an OS lock, including across app
processes. Abort/shutdown stops and joins the entire service cgroup; an unconfirmed
cleanup cannot return success. A new operation first stops any orphaned service
for that workspace. A broker killed outright may leave a worker until the service's
30-second limit or that cleanup; there is no claim of instantaneous cleanup after
SIGKILL. Forced termination of a stuck Node broker makes it unavailable until
restart. The service limits remain in force independently of Node/Pi.
The server hooks SIGTERM, SIGINT and adapter-node shutdown into one joined runtime
cleanup; this does not replace adapter-node's HTTP shutdown handlers.

Write/edit have Pi's **unsafe** replay policy. An interrupted mutation is not
automatically repeated; inspect before retrying, since it may have committed.
Atomic-write temporaries left before rename are discarded on the next inventory
only if they match the broker's reserved, owner-private regular-file pattern.
This never retries the write or deletes its destination. Failed operations may
leave newly created parent directories. Profile restore does not undo files;
forks deliberately start with empty, independent workspaces. Tool calls/results
are Pi's durable transcript entries, rendered as escaped text in the chat.

This boundary assumes a trusted kernel and local operator; it does not defend
against a malicious same-UID host process moving directories during an operation.
Workspace parents must remain quiescent. No authentication for remote/multi-user
deployment is added. The app's existing loopback restriction remains mandatory.

Policy lineage: reviewed Harness² `1bc5c61` sandbox, narrowed to a fixed Python
file worker. References: [bubblewrap security model](https://github.com/containers/bubblewrap),
[systemd transient services](https://www.freedesktop.org/software/systemd/man/255/systemd-run.html).
The resources and probe assertions above were verified locally, not inferred from
the mere presence of those binaries.

## Mojo build / run

The operator supplies **`GLIMMER_MOJO_PREFIX`**, an absolute, non-symlink installed
Mojo prefix with `bin`, `lib`, `include` and `share/max/modular.cfg`. No compiler
is downloaded, installed, activated or updated by the app. Click **Check Mojo
toolchain**, then explicitly enable `mojo`. Missing configuration leaves file
tools available but Mojo unavailable. The readiness probe runs `mojo --version`
inside the checked sandbox; it is not a claim that every source will build.

The only model argument is `{ "path": "main.mojo" }`. Under the same workspace
lock as file tools, the trusted broker launches two separate services:

1. `/opt/mojo/bin/mojo build /work/<validated-path> -o /build/program`.
2. `/build/program`, only after a zero compile exit, with a regular, non-linked
   ELF artifact and matching source identity.

There are no caller-selected flags, program arguments, stdin, environment, output
paths or executable choices. `/work` is **read-only** in both phases. A private
build directory is writable and discarded after the call; compiler cache/crash
data goes to a private 256 MiB tmpfs, never the installed toolchain. Relative
source imports can use the conversation's regular files. The compiled program
is arbitrary native code **inside the sandbox** and can spawn subprocesses there;
“no shell tool” does not mean it cannot invoke a sandboxed system executable.
No host home, model endpoint, service-manager socket, GPU device or host network
is exposed. The source and compiler runtime stay read-only.

The entry proves isolation before receiving its source path, then `exec`s the
fixed command. The host owns phase/exit status and bounds stdout/stderr separately;
program text is never parsed as a trusted result or executed on the host. Both
phases have effective limits of 4 GiB RAM, zero swap, 128 tasks, two CPU cores,
64 MiB/file and 256 descriptors. Compile wall limit: 90 seconds; run: 10 seconds;
the independent service deadline is two seconds later. Build scratch has a
256 MiB/8,192-entry/depth-32 watchdog, **not a hard disk quota**. Output is capped
at 16 KiB per stream per phase; overflow stops the service and is a failed tool
result. Oversized serialized output is additionally shortened and marked failed.

Each result keeps compile/run exit codes, bounded stdout/stderr, stop reason,
source SHA-256, checked isolation and selected compiler identity hashes (compiler,
linker, stdlib archive and config). These are **not an attestation of every shared
library or OS byte**. Rechecks detect changes to those selected files during a
call; the local operator must keep the installation quiescent. Results are not
correctness grades. Build failure never runs a leftover executable: every call
has fresh scratch. Cancellation/shutdown joins the service. Mojo uses unsafe Pi
recovery, so interrupted execution is never automatically replayed.

An outright broker SIGKILL cannot clean its private temporary build directory;
the service deadline still applies and next workspace admission stops the orphan.
Any leftover `glimmer-mojo-*` temporary directory is infrastructure residue, not
an adopted workspace or a successful output. This is the same trusted local
operator/kernel boundary as the file tools, not a general hostile-host boundary.

## Authority and integration

The loopback/same-origin endpoint is `/api/pi/profile`. JSON schemas accept only
fixed-catalog names inside profile `tools`, and reject arbitrary tool definitions,
model, provider, cwd, sandbox and extension-selection fields. Profile bodies have
an explicit 512 KiB transport ceiling, followed by the smaller content limits;
other control APIs retain their 32,000-byte ceiling. No key is involved in profiles.
Imported code and Markdown are displayed as escaped text, never raw HTML.

Native Pi instructions remain authoritative. The pinned glimmer-agent profile host
atomically writes native instructions/enabled tool registrations, its rewindable profile document
and a change entry. All app profile edits/submissions use its per-conversation
lock and version check. Do not bypass it with direct `Conversation.configure` or
`submit` calls. Existing readout and alert semantics are unchanged.

This is the existing single-process, trusted local-operator deployment, not
authentication for a public or multi-user server. Source trees must be quiescent
while snapshotting; the bounded double-read check is not a hostile-writer proof.

## Offline verification

```sh
npm run verify                  # fresh build first
npm run test:profiles           # real Pi + fake kernel, memory and SQLite
npm run test:profile-import     # path, identity, budget, inert-code controls
npm run test:profile-ui         # isolated fake-kernel browser, desktop/mobile
node scripts/profile-regression.mjs  # fresh build + existing offline regression
npm run test:file-tools          # real Pi + fake model + real file sandbox, including crash recovery
npm run test:file-sandbox        # real service isolation, bounds, cancellation and resource probes
npm run test:file-ui             # fresh production build required first, fake model only
node scripts/filetool-regression.mjs # full offline regression + both profile/tool browsers
npm run test:mojo                 # Pi + fake model + actual compiler, cwd and abort checks
npm run test:mojo-sandbox         # real compile/run and negative containment controls
node scripts/mojo-regression.mjs  # all file/application gates, then the Mojo browser
```

No live `:8091` request, GPU job, paid API call or host tool rollout was used to
build or verify this slice. Screenshots are under `preview/checks/profile-fake-*`
and `preview/checks/file-tools-*`, and remain uncommitted. The skill guidance kept the UI on the existing local
Svelte/AI-elements components; Bits UI supplies the accessible profile inspector.
