# Fixture provenance

`serve-5e4a100.json` is an unchanged copy of glimmer-agent's recorded Python
producer fixture at 6ea3078. It contains CPU-fake outputs from the actual Nomos
serve implementation, not private captured research trajectories. Its embedded
producer and kernel identities are retained. Model tests pass those responses
through the pinned agent's `TurnCheck`, then the UI's snapshot/batch/terminal
reducer. The HTTP fixture is independent test-only code and never serves live UI.
