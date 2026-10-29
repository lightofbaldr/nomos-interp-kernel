# Glimmer Lab

A private, chat-first Svelte 5 control plane for Glimmer on Nomos. Live chat now
uses a server-hosted Pi harness: durable conversations, real thinking/text,
per-token emotion projections, interpreted workspace top-k and signed steering.
Observational readout alerts are explicitly **not a lie detector**.

Start with [the live Pi guide](docs/LIVE-PI.md) for configuration, contracts,
alert statistics and limitations. [The steering-diff guide](docs/STEERING-DIFF.md)
describes the recorded Baseline | Steered | Difference explorer.

## Run

Node 24 and npm. No model or kernel service is launched by this app.

```sh
npm ci --ignore-scripts
npm run verify
# Configure the private server environment as described in docs/LIVE-PI.md.
PORT=5241 ORIGIN=http://127.0.0.1:5241 npm start
```

Production start defaults unset/empty HOST to 127.0.0.1. Wider binding is an
explicit operator opt-in and needs separate authentication/access controls.
Pi/Nomos routes require a loopback Host and same-origin browser access. This is
not a multi-user/public deployment. The browser never selects an upstream URL.

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
- `/?mode=demo` remains explicitly scripted. Imported traces remain self-reported.
  Recorded steering bundles are external, immutable local data; the default
  catalog is under `~/nomos_data/steering-diff/catalog-sampled-20261029`, not the scratch repo.
- No tools, attachments, automatic retries or automatic compaction are enabled
  in the initial local Pi host. Model text is escaped plain text.

## Checks

```sh
npm run verify
node --test --test-reporter=tap tests/live/*.test.ts
npm run test:chat                  # real browser + gateway + Pi, fake kernel
node scripts/live-regression.mjs  # demo, recorded, transport and loopback checks
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
