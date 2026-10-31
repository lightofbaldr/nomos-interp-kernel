# Harness workbench — slice A

The **Profile** tab beside Controls manages each Pi conversation's instructions,
ordered Markdown skills and imported advisory text. This slice has **no callable
tools**. File tools and a fixed-argv sandboxed Mojo build/run tool are separate,
later slices; nothing here runs a model-supplied command or extension.

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

## Authority and integration

The loopback/same-origin endpoint is `/api/pi/profile`. JSON schemas reject tool,
model, provider, cwd, sandbox and extension-selection fields. Profile bodies have
an explicit 512 KiB transport ceiling, followed by the smaller content limits;
other control APIs retain their 32,000-byte ceiling. No key is involved in profiles.
Imported code and Markdown are displayed as escaped text, never raw HTML.

Native Pi instructions remain authoritative. The pinned glimmer-agent profile host
atomically writes native instructions/empty tools, its rewindable profile document
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
```

No live `:8091` request, GPU job, paid API call or host tool rollout was used to
build or verify this slice. Screenshots are under `preview/checks/profile-fake-*`
and remain uncommitted. The skill guidance kept the UI on the existing local
Svelte/AI-elements components; Bits UI supplies the accessible profile inspector.
