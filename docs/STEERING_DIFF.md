# Workspace steering diff on the readout spine

Hackathon build order, step 1 (Adam, 10-24; "start the steering diff", 10-26). Design: Codex's
`~/Desktop/Codex/DESIGN-workspace-steering-diff-2026-10-24.md`. This note fixes how it is built on the readout spine
(Plans 1 and 2, main 5e4a100) and marks Prime's calls as **(call)**.

The question: when we steer an emotion direction, which workspace readouts change, where do they change, and how do
the changes relate to behavior? The view is **Baseline | Steered | Difference**, with a shared position and layer
selector.

## Two modes, one schedule

- **Free generation.** Each arm generates its own greedy reply. Positions are aligned only up to the first differing
  generated token. After it, the arms have separate tracks, never a position-by-position diff.
- **Matched input replay.** Both arms consume exactly the same token ids (teacher forcing). The reference sequence is
  named, normally the baseline's free-generation reply. A replay whose consumed ids differ from its reference is refused,
  never labelled matched.
- **(call) Both modes run one loop on the base path:** prefill, then one M=1 decode step per token. The next token is the
  argmax (free) or the reference's (matched). There is no DFlash and no KV store, and every arm gets a fresh prefill. The
  execution schedule is therefore identical across arms and modes. The serve's own replies use DFlash, whose verify
  batches would change the numerics, so this adapter does not.
- **(call) Route:** the Muse launcher's environment (`pyhost/serve_nomos.sh --model muse env`), which is what the serve
  runs. NOT `tools/sp1_env.sh`. One exception: NOMOS_KVSTORE=0, because the adapter never uses the store.
- **(call) Readouts:**
  - workspace at the spine's layers 36/40/44/48 with **k = 16**, of which the top 8 are displayed;
  - emotion projections at layers 20, 24, 29, 36, 44 and 51. Layer 24 is the intervention layer; layer 20 is upstream of
    it.
- **Steering** starts at the last prompt token (ruling P2-R8, as in the serve), on layer 24. That was SP1's and wsdiff
  v2's layer.

## What a difference is

For a candidate token c, layer l and position p: `delta = steered_score - baseline_score`. Scores are the kernel's
workspace scores: the lens, then the final norm, then the exact NVFP4 lm-head. They are not probabilities, and they are
not calibrated across layers.
- **Candidates** are the union of the two arms' displayed top 8.
- **A score is exact** when the token is in that arm's transmitted top 16.
- **Otherwise it is unknown**, bounded above by that arm's 16th score. The delta is then a one-sided bound, labelled
  "entered the displayed top 8" or "left the displayed top 8".
- **Never zero for unknown.** A rank is a rank within the transmitted top 16, never a full-vocabulary rank.
- **Emotion deltas** are exact at every position, because each record carries all 39 projections per layer.

## Acceptance gates (automatic; nothing is shown as evidence until they pass)

- **G1 repeat:** base and base_repeat agree on every token and every raw record byte.
- **G2 armed zero:** a zero vector armed at layer 24 agrees with base, bit for bit.
- **G3 pre-onset:** prompt positions before the onset carry identical emotion records in every arm.
- **G4 upstream invariance:** in matched replay, emotion at layers below 24 is identical across arms at every position.
  Steering at 24 cannot reach them.
- **G5 arithmetic:** the diff passes synthetic fixtures with known changes, unknowns and refusals (CPU tests).
- **G6 matched identity:** a replay consumed exactly its reference's ids.

## First investigation (wsdiff v2's frozen set, re-run live on the spine)

- **Prompts:** S1 (the driving test, failed for the third time) and the neutral T1-T4.
- **Arms:**
  - base, base_repeat, and zero (armed zero);
  - joyful at s = 0.3, 0.5, 0.7;
  - random1 at s = 0.3, 0.5, 0.7. random1 is sp1/steer.py's seed-1 standard normal unit vector, in the same s x layer
    size units.
- **Length:** max_new 256.
- **Lexicons** (celebration, sympathy_distress, practical_help) count displayed top-8 hits per arm and layer. They don't
  need score arithmetic.

## Files

- `pyhost/steerdiff.py`: the comparison (alignment, workspace and emotion deltas, lexicon counts). CPU only. It works on
  nomos.readout/1 records, so serve records and adapter records use the same code.
- `pyhost/test_steerdiff.py`: G5.
- `tools/steerdiff_capture.py`: the GPU research adapter. It runs in process, arms through the kernel's steering exports,
  assembles records with `readout.ReadoutTurn`, and checks gates G1-G4 and G6.
- `tools/steerdiff_view.py`: a self-contained HTML viewer (Baseline | Steered | Difference).

Outputs go to `~/nomos_data/steering-diff/`. Every comparison names both source runs, the kernel and library identity,
the asset IDs, the tokenizer hash and the route.
