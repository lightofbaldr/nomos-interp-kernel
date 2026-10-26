# Muse on Nomos

Glimmer Lab review branch: start with [IMPORT.md](IMPORT.md) for the fresh
snapshot's provenance and [the steering-diff guide](docs/STEERING-DIFF.md) for
the integrated recorded-data route. The original setup below is retained as
source context; port 5226 still belongs to design-kits, not this checkout.

A **chat-first, dark Svelte 5 application prototype** with read-only workspace and emotion-concept inspection. Local preview: http://127.0.0.1:5226/. An independent Nomos frontend inspired by the Muse/Meta blue-and-dark direction, not an official Meta product.

With `NOMOS_API_BASE_URL` configured, the app opens a blank **Live kernel** conversation and connects to the already-running Glimmer serve. Answers and real `reasoning_content` stream separately. Health and KV session-store counters come from the server; workspace/emotion probes and steering are **not connected**. Offline kernels disable sending rather than falling back to scripted output.

**Demo replay** remains explicitly scripted, including every concept, emotion score, position and elapsed-time value; `/?mode=demo` opens it intentionally. **Recorded trace** is an imported, self-reported record, never automatically live or attested. Demo/recorded browsing never sends a chat request to the kernel.

## Run

Node 24 and npm. Local library archives are included under `vendor/`.

```sh
npm ci --ignore-scripts --allow-remote=all
npm run dev
```

The npm 12 opt-in is scoped to this command for registry tarball URL specifications; no global npm settings are changed. The app includes a lockfile and locally packed libraries. Review install inputs before deploying elsewhere.

```sh
npm run verify
HOST=127.0.0.1 PORT=5226 ORIGIN=http://127.0.0.1:5226 node build/index.js
```

For live chat, set the private server environment (not a public browser variable):

```sh
NOMOS_API_BASE_URL=http://127.0.0.1:8091 HOST=127.0.0.1 PORT=5226 ORIGIN=http://127.0.0.1:5226 node build/index.js
```

The local ignored `.env` contains this workstation URL for `npm run dev`; the managed preview refresh helper reads that file and forwards the setting explicitly. A standalone adapter-node process does not load `.env` automatically. Do not point this app at a public/untrusted model server or expose its unauthenticated proxy publicly.

## Features

- Three scripted conversations, local prompt submission and a cancellable response replay.
- Real multi-turn Glimmer chat, expandable provider reasoning, Stop propagated to the upstream, explicit interrupted/length-limited reply status, and raw partial history retained after Stop.
- Fixed reasoning-high system message, temperature 0 and verbatim assistant history per [KERNEL_CONTRACT.md](docs/KERNEL_CONTRACT.md). No background title/suggestion generations.
- Server-reported model/readiness/DFlash-loaded/store state; real cumulative KV counters fetched between turns only. Browser-observed first-text/round-trip timing is labelled separately from kernel throughput.
- Chat-only mode or a right-side inspector; phone inspection uses a Bits UI focus-managed sheet.
- Exact trace-frame freeze/follow/scrub, layer selection, signed concept/emotion scores and provenance.
- Client-only, bounded JSON trace import and export. Chats are in memory only and reset on reload, new conversation or switching away from live mode; prompts and full history are sent to the configured kernel only when submitting live chat.

- Native Enter/Shift+Enter composer, keyboard tabs/dialogs, Escape, local fonts, reduced-motion fallback.
- Emotion-steering Dock beneath the composer: Joy, Calm, Sadness, Fear, Anger, Desperation and Compassion are independent on/off preview targets. Blue outlines and checkmarks persist through chat/replay/source changes until toggled off or cleared. Reload starts all off. Seven labeled 60px targets wrap into two rows on small phones. The Dock always says **Not applied**; no steering backend is configured.
- Ordinary local AI Elements Message/PromptInput components, Magic Dock/BlurFade/SparklesText and Fancy Letter3DSwap. Click the model title to pause/resume its small blue sparkles; reduced motion keeps it static. The Dock magnifies on desktop pointer proximity, but stays static on narrow/touch screens and with reduced motion. No remote registry JSON or browser custom-element wrappers.

## Hookup and scientific limits

The browser's memory-only history is not a promise that the workstation retains nothing: the kernel's own KV session store can persist cache data under its configured policy.

See [INTEGRATION.md](docs/INTEGRATION.md) for the **proposed**, versioned trace format and the boundary between chat and telemetry. It is a frontend file contract, not an implemented/approved kernel broadcast protocol.

The Dock is UI intent only, not activation steering. No emotion-to-risk mapping, safety verdict, threshold, actual intervention or policy enforcement is implemented. Selecting a target does not modify answers, scores, the mascot or trace exports. Scores are raw signed cosine values, not probabilities or subjective feelings. The workspace visualization connects the **listed concepts** to a position; the connecting lines are layout, not measured causal edges. A trace is not loaded-binary attestation. Missing data is unknown, not zero.

Authentication, durable chat storage, retry/reconnect recovery, server-issued binary/probe attestation, calibrated probes and safety-outcome validation remain integration work. Reasoning is always requested on and never synthesized. This first adapter displays model text as escaped plain text, not executable HTML or rendered Markdown; raw strings are sent back unchanged. No tools, attachments or automatic retries are enabled.

## Checks

```sh
npm run test:contract
npm run test:browser
npm run test:ui
npm run test:contrast
npm run test:live
npm run test:transport
npm run test:chat
npm run test:kernel
```

`test:transport` and `test:chat` use an isolated CPU-only fake kernel, including failure injection, UTF-8/CRLF fragmentation, exact-history and cancellation controls. `test:kernel` deliberately sends one bounded real prompt through the managed preview; it requires the workstation to be ready and records real reasoning/Paris/natural termination. It is not a model-quality or losslessness gate.

Browser/UI checks launch and clean up an ephemeral adapter-node server, using Chromium with GPU disabled. `test:live` only reads the managed 5226 preview. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` on a different workstation. Rebuild/restart the owned server before a live check; never use an old process as evidence for a new build.

The local preview is in tmux `nomos-muse-preview`. After a build, `npm run refresh:preview` validates its project marker, PID working directory, executable arguments and loopback environment before restarting only that owned pane. If the session has stopped, it creates a new dedicated preview only after proving port 5226 is vacant; it never kills an unmanaged listener. It refuses differently configured servers and forwards the private upstream setting explicitly. This Linux/tmux helper is not a VM service manager.

[GATES.md](GATES.md) records acceptance. Screenshots are real browser captures under `preview/`. Tests are scoped evidence, not full accessibility, security, clinical or production certification. Current npm installation reports low-severity transitive advisories; no dependency-security clearance is claimed. No kernel files or running model processes were changed.

Component/font provenance is in [ATTRIBUTION.md](docs/ATTRIBUTION.md); design decisions and remaining boundaries are in [REVIEW.md](docs/REVIEW.md).
