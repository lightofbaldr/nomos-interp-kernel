# Harness workbench — design for review

2026-10-30. **Proposal only; no implementation or live calls.** Based on glimmer-lab
`4d8618d`, glimmer-agent `a287b8c`, installed Pi Durable **1.3.0**, and Harness²
`1bc5c61`'s sandbox. Existing conversations keep their current instructions and
empty tool list unless the operator changes them.

## 1. Use Pi's agent state, not another harness

Pi already stores instructions and enabled tool names in the rewindable `pi.agent`
document. Use its public `configure(tx, conversationId, change)` inside a conversation
commit. Register only our fixed, reviewed extensions; select tools explicitly with
arrays, never `null` (which restores host defaults). The operator cannot change the
model/provider, extension registry, filesystem root or sandbox policy through a profile.

Durable 1.3.0 has **no native skills field or SKILL.md loader**. Use its supported
extension sections and `defineDoc` for that missing application state: a rewindable,
`fork: 'asOf'` profile document with ordered skill text, enable flags, provenance,
revision/digest and a pending draft. Instructions/tools remain authoritative in
`pi.agent`; activating a revision changes both documents and appends a non-model
`profile.changed` entry in **one commit**. Enabled skills render as bounded text
sections before instructions. No implicit discovery from home, cwd or `.pi` folders.

Each model request gets a non-model `profile.used` record naming the actual revision,
content digest and catalog/policy version, linked to its generation/request and the
readout request ID when present. Pi's own `pi.system` entries retain the effective
prompt/tool changes. Historical turns without this metadata say **profile unrecorded**;
they never inherit today's label. Restore creates a new revision from `snapshotAsOf`;
forks use the applied profile at the selected entry and clear pending drafts.

**Timing adjustment requested:** Pi freezes prompt/tools at request preparation, but
resolves tool implementations and environments again during tool execution. Therefore
save edits during a live turn as **pending**, and activate only once its complete
model/tool run settles, before the next user submission. Do not swap capabilities
between rounds of that run. This is narrower than “next internal model request.”
Use the existing submission admission plus a per-conversation apply/submit lock and
revision conflict check, including after restart; a draft never silently overwrites
a newer one. Readout/steering semantics stay unchanged.

## 2. Small, sandboxed tool catalog

First version: `read`, `write`, `edit`, `ls`, `find`, `grep`, all off by default.
Use Pi tool registrations and durable tool results, with fixed schemas and trusted
broker callbacks. **All model-requested filesystem work runs in a sandbox worker**,
not Node's host filesystem environment. Do not install `CodingTools` with
`NodeExecutionEnv`, a raw host shell, MCP loaders or a plugin loader.

Reuse Harness²'s reviewed launcher policy in a versioned server-owned broker, not
by importing a mutable live run checkout: a transient user **service**, bwrap
namespaces, `clearenv`, dropped capabilities, no network, no host home/secrets or
model socket, read-only runtime, and only this conversation's workspace persistently
writable. Workspace paths are derived server-side from the conversation ID; cwd is
virtual `/work`. A fork gets a distinct workspace. Profile rewind does **not** undo
filesystem effects, and the UI says so.

Use structured bounded input, not shell interpolation. Worker-side descriptor-relative,
no-follow operations reject traversal, symlinks, special files and paths outside
`/work`; a host `realpath` precheck alone is insufficient. Serialize mutations and
bound reads/searches/output. Proposed ceilings: 1 MiB per text file, 64 KiB per result,
256 MiB/8,192 workspace entries, 30 s per operation; retain the sandbox's cgroup
memory/process and file-size limits. The workspace watchdog is **not a hard disk
quota**. Abort/shutdown stops and joins the complete service cgroup. Writes/edits use
Pi's unsafe-replay policy: interrupted effects are reported, never silently repeated.

Missing bwrap, user-service limits or a failed isolation preflight means **tools
unavailable**, with no host fallback. Instructions/skills-only chat can still work.
**Bash and compilers are omitted from this first catalog**, not presented as working
toggles. They require a separate bounded addition after the file-tool boundary passes.

## 3. Skills and Harness² import are data

The panel supports adding/removing/enabling/disabling Markdown skills. Validate
frontmatter name/description, unique names, UTF-8 and bounded sizes; proposed caps
are 16 skills, 16 KiB per skill and 64 KiB total rendered profile text. Render enabled
bodies inline for this first version, so skills work with file tools disabled. This is
not coding-agent-style progressive loading. Removed content remains available to
recorded revisions; removal is not deletion of history.

Import accepts a directory only beneath operator-configured import roots, never an
arbitrary host-file browser. Snapshot bounded regular files with no-follow traversal,
hashes and a before/after inventory check; refuse changed sources. Never write into
Harness² roots. Apply only after preview and operator confirmation:

- `systemprompt.md` → native instructions.
- `skills/*/SKILL.md` → skill entries, with explicit enable choices.
- `guardrails.md` and `tool_descriptions/*.md` → named, ordered prompt sections;
  these are **advisory text**, not enforced guardrails or replacement tool schemas.
- `extensions/*.ts` → escaped, read-only code preview, **never loaded or executed**.
  Scripts, skill resources, memory and other files are listed as not imported/active;
  the preview warns about missing capabilities instead of claiming exact equivalence.

No imports, hooks, dependency installation, interpolation or code evaluation from
that tree. Text/code previews do not render raw HTML. Record source path, file hashes
and omissions. Diff against a separately selected base-harness snapshot, or against
the current profile with that different reference clearly labelled. This imports a
**text/profile subset**, not a faithful replay of the full Harness² executor.

## 4. UI and delivery

Add an **Agent profile** panel beside existing controls: instructions, skill cards,
fixed-catalog toggles, import/diff preview, and distinct **Active / Pending / Unsaved**
states. Switching sessions reloads that session's state. Every turn shows its used
revision and an inspectable effective profile. Keep the loopback/same-origin API
boundary, strict schemas and bounded bodies; no keys or host-path access from tools.

After approval, glimmer-agent gets the small Pi adapter/profile/bridge additions;
glimmer-lab owns the broker, importer, operator API and UI. Vendored Pi stays read-only.
Use separate review branches and pinned package updates; no merge without Adam.

Offline acceptance tests will cover reload/fork/restore, atomic activation and stale
edits, edits during held tool rounds, exact per-request attribution, unchanged legacy
defaults, session switching, sandbox failure/abort/recovery, traversal and symlink-race
attempts, cross-session access, resource limits, and an imported extension with a
host-write sentinel that must never run. No build or test may contact `:8091` yet;
live E2E remains a separately coordinated step.

**Review choices:** approve turn-boundary activation, file-tools-only v1, and the
explicit Markdown/text-only import subset before implementation.

### Local API evidence

- `src/lib/server/pi-runtime.ts`: current `create()`, admission and fixed tool list.
- Pi Durable 1.3.0 `dist/harness/agent.js`: rewindable `AgentDoc`, public transactional
  `configure`; `dist/harness/types.d.ts`: `AgentChange`, sections and hooks.
- `dist/harness/generation.js`: preparation freezes prompt/tools before `beforeRequest`;
  `dist/harness/tool.js`: later agent resolution and unsafe-replay handling.
- Durable README, “Per-Conversation Agent”, “Environment”, “Your Own State”; its
  exported types contain no skills property. `glimmer-agent/src/pi.ts` remains the
  integration's sole Durable import boundary and will re-export needed public APIs.
- Harness² `harness2/substrate/sandbox.py` and `harness2/substrate/pi.py`: containment
  policy and the fuller upstream harness composition being intentionally subsetted.
