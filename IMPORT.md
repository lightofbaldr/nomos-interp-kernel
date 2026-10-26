# Glimmer Lab — private snapshot import

This is a **fresh repository**, not a fork or a history transfer. Its initial
application is the working snapshot of `ports/nomos-muse` in
`/home/adam/Projects/design-kits`, based on
`ecb0850b8a3af600bf6a8833ccc6739e9d7dde99`.

The source had uncommitted changes. [The per-file manifest](docs/import/snapshot.json)
records their hashes, committed-base hashes and modified/untracked status.
It includes pre-existing Glimmer live-chat/serve plumbing and UI work. Authorship
could not be established from the inspected source documentation, so it is
explicitly **unknown**, not attributed to this import session. File modification
times are metadata, not evidence of authorship. All 52 imported files retain
their working-copy bytes. The source repository and its Forgejo remote were not
modified; none of its Git history is present here.

The three `vendor/*.tgz` packages are the port's existing local Svelte dependencies,
including their licenses and attribution. No capture bundles, environment files,
preview screenshots, application build output or installed dependencies were
imported. The root commit adds this document, provenance, verification scripts
and the migration ledger alongside the unchanged snapshot.

## Running and checking this copy

Use a separate port; the old application's preview on **5226 belongs to the
source checkout**. Do not use the inherited `refresh:preview`, `test:live` or
`test:kernel` scripts for migration verification. They concern that historical
local setup or make real model requests. The inherited `README.md` and `GATES.md`
are historical snapshot documents, not evidence that this checkout passed them.

```sh
npm ci --ignore-scripts
npm run verify
npm run test:contract
npm run test:transport
npm run test:chat
HOST=127.0.0.1 PORT=5241 ORIGIN=http://127.0.0.1:5241 node build/index.js
```

With no private upstream setting, the application is a labelled demo. The
transport and chat checks use an isolated CPU fake, never the live kernel.
`MIGRATION-GATES.md` is this migration's acceptance ledger. Git uses the local
identity `Light of Baldr <oss@lightofbaldr.dev>`; inspect every reachable commit
before pushing. Only the private GitHub repository is a push destination.

The steering-diff integration belongs on a separate review branch. It is not an
implicit approval to merge or publish either the application or its data.
