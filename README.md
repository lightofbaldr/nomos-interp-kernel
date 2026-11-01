# Glimmer Lab

A private, chat-first Svelte 5 control plane for Glimmer on Nomos. Live chat now
uses a server-hosted Pi harness: durable conversations, real thinking/text,
per-token emotion projections, interpreted workspace top-k and signed steering.
Observational readout alerts are explicitly **not a lie detector**.

Start with [the live Pi guide](docs/LIVE-PI.md) for configuration, contracts,
alert statistics and limitations. [The steering-diff guide](docs/STEERING-DIFF.md)
describes the recorded Baseline | Steered | Difference explorer.
The [harness workbench](docs/HARNESS-WORKBENCH.md) adds per-conversation
instructions, inline skills, a text-only Harness² import and recorded profile
attribution. Optional file tools and a fixed-argument Mojo build/run tool run
only in checked, resource-limited Linux sandboxes. No shell tool is exposed.

The [video workspace checkpoint](docs/VIDEO-WORKSPACE.md) separates minimal chat,
saved conversation history, the token-by-token Global Workspace and the harness
workbench. The recorded workspace opens without a model request. Statistics,
Interpreter and System are available as separate pages.
The [Step 2 recording guide](docs/WORKSPACE-STEP2.md) covers the 48 system-prompt
examples, their classifier annotations, and the separate registered/post-hoc windows.

## Run

The supported runtime is **Node 24.22.1 with bundled Undici 7.30.1**, plus npm.
`.nvmrc`, `engines.node`, build/start checks and the server startup hook enforce
that exact reviewed combination. No model or kernel service is launched by this app.

```sh
nvm use                         # selects the already-installed .nvmrc version
npm ci --ignore-scripts
npm run check:runtime
npm run check:dependencies
npm run verify
# Configure the private server environment as described in docs/LIVE-PI.md.
PORT=5241 ORIGIN=http://127.0.0.1:5241 npm start
```

Production start defaults unset/empty HOST to 127.0.0.1. Wider binding is an
explicit operator opt-in and needs separate authentication/access controls.
Pi/Nomos routes require a loopback Host and same-origin browser access. This is
not a multi-user/public deployment. The browser never selects an upstream URL.

`npm start` uses `scripts/start.sh`: an explicit absolute `GLIMMER_NODE_BIN`,
otherwise the installed nvm version from `.nvmrc`, otherwise PATH's Node. The
selected executable must pass the exact Node **and built-in Undici** checks
before loading the adapter. An explicit bad path/version refuses without falling
back. Nothing is installed, and shell profiles are not sourced. For non-nvm
deployments, set `GLIMMER_NODE_BIN` to an installed supported binary. Use that same
runtime on PATH for `npm ci`, development and verification; the legacy refresh
helper also checks its interpreter before any tmux operation.

The separate npm Undici dependency is overridden and locked at **6.29.1**.
That override does not repair an older Node's built-in fetch. See the
[reachability and patch record](docs/UNDICI-REACHABILITY-2026-10-30.md).

The legacy dev command uses loopback port 5226, which may belong to the original
design-kits preview on this workstation; choose an unused port explicitly:
`npm run dev -- --port 5241`. Do not restart that original preview. The inherited
`refresh:preview` helper and `test:live` target the legacy 5226 preview, not this
review branch; they are not the live-Pi acceptance commands.

## What is live and what is not

- Live sessions persist in server-side SQLite. New chat, navigation and browser
  disconnect leave other sessions alive. Stop explicitly aborts a generation.
- Readouts use the pinned glimmer-agent dependency. Missing signals stay unknown;
  no illustrative values appear in live mode. Settings are versioned and apply
  to the next request. Turn headers identify the settings actually used.
- The emotion dock shows raw signed alignment, not emotion probabilities.
  Workspace token ranks are within the supplied top-k, not the whole vocabulary.
  Token labels use an operator-pinned local tokenizer. These are interpretations,
  not proof of knowledge, consciousness, truthfulness or safety.
- Alerts are page-local rules over measured reasoning positions. Their statistics,
  warm-up, gap behavior and token-prefix matching are documented in the UI/guide.
- `/preview?mode=demo` retains the explicitly scripted legacy preview, outside the
  real conversation history. `/preview` retains the earlier all-in-one workbench.
  Imported traces remain self-reported.
  Recorded steering bundles are external, immutable local data; the default
  catalog is under `~/nomos_data/steering-diff/catalog-sampled-20261029`, not the scratch repo.
- Greedy decoding is explicit and remains the default. Each conversation can
  opt into slower sampling; each reply records its own decoding and 6,144-token
  limit. A capped reasoning-only reply offers an explicit Retry without adding
  another copy of the question. See [decoding and Retry](docs/LIVE-PI.md#decoding-and-an-explicit-retry).
- No tools or attachments are enabled in the initial session. Automatic retries
  and automatic compaction stay off. Model text is escaped plain text.

## Checks

```sh
npm run verify
node --test --test-reporter=tap tests/live/*.test.ts
npm run test:chat                  # real browser + gateway + Pi, fake kernel
node scripts/live-regression.mjs  # demo, recorded, transport and loopback checks
node scripts/workspace-tests.mjs  # exact positions, ranks, unknowns and durable titles
node scripts/workspace-browser.mjs # new shell/history/workbench, fake kernel only
node scripts/workspace-browser.mjs --recorded # requires the pinned local captures
node scripts/step2-tests.mjs      # synthetic pinned-input and marker controls
node scripts/step2-assets.mjs     # reconcile all 48 external recordings, read-only
node scripts/step2-browser.mjs    # saved-data browser; build first, no model call
npm run test:kernel                # explicit GPU client test against existing :8091
```

The real gate checks for other clients, sends a bounded UI conversation, then
runs the original two-turn Pi E2E client without its serve-down launcher. It never
starts/stops/restarts the serve. It is an integration check, not a quality,
mechanism or performance claim. Local screenshots/results are not committed.

## Provenance

[IMPORT.md](IMPORT.md) records the fresh snapshot import from design-kits.
[ATTRIBUTION.md](docs/ATTRIBUTION.md) records component/font sources. Historical
integration proposals, old chat test scripts and root GATES.md remain as the
import record; they are superseded for live mode by docs/LIVE-PI.md and the new
checks above. No capture data, build output or model assets are committed.
