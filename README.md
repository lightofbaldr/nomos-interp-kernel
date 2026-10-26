# glimmer-agent

The Glimmer agent's Pi harness: the `nomos` provider (readout requests to the Nomos serve), the pi-durable readout
extension (settings as rewindable documents, records in the transcript and never in the model's context), and the
bridge that streams a conversation to the UI and the reports.

Design: glimmer-emotions `docs/superpowers/specs/2026-10-20-readout-spine-design.md`, section 4.

    npm ci && npm test && npm run typecheck
