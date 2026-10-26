# Measurement traps

On 2026-08-02 this repo produced, in one session, four defects that did not exist. Every one was
reported by an instrument, and every one was caught by a control or an instrument check — never by
reasoning harder about the code. That day also produced one defect that *did* exist and was
invisible to every instrument we had pointed at it.

This file is the catalogue. It is not a diary; each entry is here because the trap is structural
and will recur.

## The rules, first

1. **A cell without its own control is not a measurement, it is a number.** If you cannot name the
   run that differs from it in exactly one variable, you cannot attribute what you are seeing.
2. **A null refutes the fix, not the diagnosis.** When a change measures flat, ask which *quantity*
   it actually reduced before discarding the mechanism. The lm-head was correctly diagnosed as
   re-reading the activation ~704 MB per token; shared-memory staging then measured null and the
   *mechanism* was wrongly discarded. The limiter was load **instructions** (`lg_throttle`), not
   bytes, and staging reduced neither. That conflation cost five subsequent nulls. The same lesson
   pointing the other way: profiling the int8 read path showed `deq` **flat at 1 ms** while
   attention went 6 → 25 ms — optimising the dequant would have bought exactly nothing.
3. **When a flag changes what is WRITTEN, check every READER.** An unconditional write plus a
   reader that ignores it does not degrade — it corrupts, silently.
4. **Never quote a quant-flag number without naming the box AND the code path.** The same flag has
   measured −3.5%, +8.7%, +9.5% and +15.7% on different hardware and read paths.
5. **A reference that is wrong for the box is worse than no reference.**
6. **Prefer the failure that shouts.** Given a choice between a guard that can silently not fire and
   a construction that cannot be wrong, take the construction.
7. **Measurement-context comparability (Prime's consolidation, 2026-08-04): a correctly-measured
   number is still the WRONG number when the context differs.** NTOK, prompt mix, and run length
   are three axes of one rule, not three separate lessons — it was written three times in one
   week (a 48-vs-64 NTOK sweep mixup, a non-comparable historical record, a 64-token bench read
   against a 1024-token serve) before being stated once. The full context is six axes: box, code
   path, entry point, weight bytes, prompt set, generation length. The companion guard:
   comparisons that SHARE their context survive — do not over-correct by voiding same-bar,
   same-NTOK results when only a cross-context read was invalid.
8. **Priors establish that a path COULD hide a fault, not that it HAD one** (Prime, 2026-08-04,
   registering his own miss). Two true priors about the int4-KV path (an invalid debug
   instrument; a sibling OOB) were collapsed into "the fault is there" — and a one-flag
   discriminator killed the hypothesis in minutes. Reason from priors to EXPERIMENTS, never to
   conclusions.
9. **Before building an optimisation, prove it CAN move the binding quantity.** A change can be
   correct in principle, follow a real law, and still be a guaranteed no-op — and the build is the
   most expensive way to discover that. Two lines of arithmetic retire the work item. See the class
   below.

## A separate class: the change that cannot move the binding quantity

**An optimisation is only an optimisation if the quantity it improves is the one that binds.**
Correctness of the principle is not evidence of effect. This is cheap to check and expensive to skip,
because the change usually *sounds* right — it invokes a real law that holds elsewhere in the system.

Worked example, 2026-08-03 — converting the DFlash drafter body from Q4_0 to NVFP4, which the
project's own per-arch precision law endorses ("NVFP4 on discrete Blackwell") and which had just
correctly killed an 8-bit drafter arm. All three claimed axes were nulls *before any code was
written*:

| claimed axis | check | result |
|---|---|---|
| space | bits/value | Q4_0 = 32 vals/(16 B + 2 B fp16) = **4.500**; NVFP4 = 16 vals/(8 B + 1 B e4m3) = **4.500**. Identical. |
| speed | roofline regime | body is weight-stationary at S=16, AI ≈ 57 flop/byte vs ≈ 416 machine balance ⇒ **memory-bound**. Native FP4 MMA is a *compute* win with nothing to buy. |
| quality | prior A/B | Q4→Q8 (double the precision) moved E 3.01 → 3.05. NVFP4 is a strictly smaller delta than a measured null. |

The generalisation, and the two questions that catch it:

- **What quantity does this change reduce, numerically?** Compute it. Bits/value, bytes moved,
  instructions issued, launches. If the number is unchanged, stop.
- **Is that quantity the one that binds?** Establish the regime first (memory- vs compute- vs
  issue-bound). Rule 2 above is the same error found *after* building; this is the same error caught
  *before*.

### The sub-trap inside that second question: AI is a property of the KERNEL, not the shape

Establishing the regime is itself easy to get wrong, and it cost a refuted hypothesis the same
evening, 2026-08-03. The reasoning was: the drafter body at S=16 is weight-stationary, so
arithmetic intensity = 16 rows × 2 flop / 0.5625 bytes ≈ **57 flop/byte** against a ~416 flop/byte
machine balance ⇒ **memory-bound** ⇒ MMQ's tensor-core advantage is unspendable ⇒ route to the
GEMV path instead. Measured:

| drafter route | draft ms/cycle | weighted tok/s | E |
|---|---|---|---|
| MMQ (shipped) | **10.02** | **15.99** | 3.10 |
| v4 GEMV (forced) | 38.29 | 13.90 | 3.10 |

v4 was **3.8× slower**. The hypothesis was backwards.

**Why:** that AI number assumes each weight is read once and reused across all 16 rows. *That reuse
is something a kernel either achieves or does not.* MMQ stages a weight tile once and serves all 16
rows from it; the GEMV-shaped route does not get that reuse at S=16, so its **effective** AI is far
lower and it moves far more traffic. The calculation described an *ideal* kernel and was then used
to pick a *real* one.

- **Before calling a GEMM memory-bound, confirm the kernel actually attains the reuse the AI
  assumes.** Ideal AI is an upper bound on the implementation, not a description of it.
- Compounding error, and our own rule 4 turned on its author: the 166 vs 208 GB/s figures used to
  rank the two routes were measured at the **verify** shapes (S=5..8), then applied to the
  **drafter** shapes (S=16). Never move a number across code paths.
- What survived and is worth keeping: **E was identical (3.10) across both arms**, so the route
  change was pure performance with no quality effect — a clean control; and the cycle model
  predicted 3.10 ÷ 0.232 s = 13.4 tok/s against 13.90 measured, so `tok/s = E ÷ cycle_time` is
  tight enough to size levers with.

Corollary, and the reason this is worth doing rather than skipping: **chasing the wrong lever is
often what produces the right measurement.** Asking "what would NVFP4 actually save here?" forced
the first real profile of the draft path — 6.4 ms against a 2.0 ms weight-streaming floor, 31% of
roofline — which is a genuine 3× headroom nobody had looked at. Do the arithmetic, take the null,
keep the measurement.

## The class: a green correctness gate says nothing about the CONFIGURATION

**Losslessness proves the math, not the setup.** A byte-exact gate compares spec-decode against base
decode *in the same process, under the same environment*. Both sides inherit every flag. So a
missing **performance** flag is invisible to it by construction — the gate stays green while every
number in the run describes a configuration nobody ships.

**2026-08-03, the whole evening.** A hand-typed GB10 env omitted four flags that live in `./nomos`'s
arch-common `BASE_ENV` (`NOMOS_VERIFY_MMQ_SMALL`, `NOMOS_VERIFY_BLOCK_ATTN`,
`NOMOS_VERIFY_DECODE_ORDER_ATTN`, `NOMOS_VERIFY_DECODE_ORDER_MULTIROW_ATTN`). Without `MMQ_SMALL`,
S=5..8 verify falls off the tensor-core MMQ body onto the GEMV route. Same box, same commit, same
prompts, **12/12 lossless on every single arm, both ways**:

| SPEC_VB | hand-rolled env | `./nomos env` | verify marginal |
|---|---|---|---|
| 5 | 19.99 | 22.05 | |
| 7 | 15.84 | 22.81 | |
| 9 | — | **23.73** | |
| | ~22 ms/row | **~1–3 ms/row** | **10× apart** |

The optimum *inverted*: shallow VB won on the broken env, deep VB won on the real one. An entire
evening of "findings" — a verify-cost model, a superlinearity claim, a proposed default change —
described the env, not the kernel. And `./nomos` had the answer in a source comment the whole time:
*"That cliff is why VB=4 looked optimal: every sweep hit it."* It had already caught someone in
July, and the doc block being followed omitted the fix.

**Rules that fall out:**

- **Never hand-copy an env block. Derive it.** `eval "$(./nomos env | ...)"`, and **assert** on what
  arrives — the re-run aborts if fewer than four `NOMOS_VERIFY_*` flags survive. A config typo must
  fail loudly, not silently produce a plausible number (rule 6).
- **Publish a fingerprint for known cliffs, not just the fix.** "Verify ≳140 ms @S=6 or ≳180 ms @S=8
  on GB10 means you are in the GEMV cliff" turns a subtle misconfiguration into a recognisable log
  line.
- **When a result contradicts a previously gated one on the SAME box, suspect the config first.**
  July gated VB=9 with 3× paired interleaved A/B and a flat canary. Tonight said VB=3-5. The older,
  better-controlled experiment deserved the presumption — and the disagreement itself was the signal.
- **A canary in band does not clear you.** Base decode sat at 10.70, comfortably inside the
  10.5–11.0 constant, all night. It catches *contention*, not *configuration*.

## The cheapest tripwire we have: A RESULT THAT DISAGREES WITH YESTERDAY

**Every control in this document is *within-run* — interleaved arms, self-noise floors, order
permutation, pre-registered thresholds. All of them pass happily on a contaminated environment,
because the contamination is constant across the whole run.** The one check that catches a leaked
flag is a *cross-session* invariant: re-measure something you already measured, and demand it
reproduce.

**2026-08-04 / 05, the same failure one day apart, on the same flag.**

- *Kvasir:* a hand-typed GB10 env silently omitted four `NOMOS_VERIFY_*` flags. An entire evening's
  SPEC_VB conclusions inverted, while the 12/12 lossless gate stayed green throughout — it inherits
  the same env on both sides, so a missing performance flag is invisible to it by construction.
- *Prime:* `.host_env` still carried `NOMOS_Q4_DP4A=1` from the previous day's *clean*-arm test, so
  a block-1 measurement ran on the **int8** reader when the original degeneration was **bf16**.
  Caught only because a known prompt (Halvorsen) came back `//`×0 when it had previously been
  `//`×13. On the corrected arm it reproduced at `//`×13, rep=8, exactly.

Both are the same shape: **you change the variable you meant to change, and leave one changed from
an earlier experiment.** Neither an interleaved A/B nor a zero noise floor can see it.

**Rules that fall out:**

- **Carry a cross-session canary.** One prompt, one metric, one number you have measured before, run
  at the top of every session. Disagreement with yesterday means the environment moved, not the
  kernel — check the env before believing anything else in the run.
- **Derive the env, never inherit it.** A persistent `.host_env` / `.nomos_host_env` is a leak
  waiting to happen; derive from `./nomos env` and *assert* on what arrives.
- **A known-good result that changes is worth more than a new result that looks good.** The second
  is what you wanted; the first is the only one that can tell you your instrument moved.

(Attribution: the tripwire formulation is Prime's, from catching his own contaminated arm before
reporting it — 2026-08-05.)

## The class: THE INSTRUMENT'S OUTPUT SURFACE IS PART OF THE INSTRUMENT

**A measurement can be real at one layer and absent at the layer you are reading.** Four instances
in two days, at four different layers of the stack — three of them found by nexus in a single
afternoon while chasing the chunked-prefill defect (#74), one by me the day before.

| layer read | what it said | what was true |
|---|---|---|
| HTTP status | `200` ×3 → "ceiling lifted, 6136 tok OK" | output was `<pad><pad>…` |
| decoded text | `<pad>` → "a token-selection bug" | argmax over an **all-NaN** vector landing on index 0 |
| `%.6f` float | `0.000000` → "bit-exact agreement" | **9.923e-16** — the seed of a 16.5-logit divergence |
| zero-init buffer | `zeros` → "the lm-head computed zeros" | **nothing was ever written** |

**1. HTTP 200 is a TRANSPORT fact, not a CONTENT fact.** (nexus) A harness asserting on status codes
scores a garbage response as a pass. He reported "ceiling lifted" off three 200s and had to retract
after reading the decoded text. **Assert on the innermost artifact you can reach: status code <
decoded text < the logits array.** He only found the NaN because he stopped reading strings.

**2. Identical output from different inputs is an INSTRUMENT FAILURE, not a result.** (nexus) Three
prompts — 36, 6032, 6031 tokens — returned byte-identical logits (md5 `d07aa095ed`) while genuinely
burning 0.25s / 12.82s / 12.78s. Different compute, identical output is impossible. **Timing could
not catch it, because the compute was real — only the writeback was missing.** Hash your outputs;
distinct inputs must produce distinct hashes, and identical inputs must reproduce.

**3. Initialise output buffers with a POISON value, never zero.** (nexus — the sharpest of the
three) Zero is a plausible *computed* result, so a zero-initialised buffer structurally cannot
distinguish **"not written"** from **"written zero"**. That ambiguity sent two people down a wrong
mechanism: an entire causal chain (zero hidden → lm-head zeros → RMSNorm `1/sqrt(mean(x²))` → NaN →
poisoned scratch) was built on "the request returned zeros", and a `-12345.0` sentinel falsified it
in thirty seconds by showing the buffer was untouched.
```python
lg = np.full(VOCAB, -12345.0, dtype=np.float32)   # NOT np.zeros
```

**4. Display precision** — see the next section; `%.6f` renders any difference below 5e-7 as
`0.000000`, and that formatted zero was used as *evidence* against the hypothesis that turned out
correct.

**The general rule:** every one of these is a place where the *reporting surface* silently discards
the distinction you are trying to measure. Before trusting a null — "no difference", "no change",
"clean" — ask **what would this instrument print if the thing I am looking for were present?** If
the answer is "the same thing", the instrument cannot answer the question, however green it looks.

## The class: DISPLAY PRECISION destroys the signal — "0.000000" is not zero

**A formatted number is not a measurement. `%.6f` turns every difference below 5e-7 into
`0.000000`, and `cos` below 5e-7 of 1.0 into `1.000000`.** If the thing you are hunting is a
floating-point seed, your own print statement can hide it — and the output will look like the
cleanest possible result.

**2026-08-04, chasing the prefill-route divergence.** The stage bisect printed:

```
 6  0  attn_out (softmax+PV)   0.000000   1.000000   n_diff=1  <<<
```

Read as bit-exact agreement, a dozen times, across several hours. Reprinted in scientific notation
the same cell is:

```
 6  0  attn_out (softmax+PV)   9.923e-16   1.000000000   n_diff=1
```

**One element differing by ~1e-15** — the seed of a divergence that reached **16.5 logit units** by
the output layer (~1.7e16 total gain over 60 residual layers ≈ 1.86× per layer, an ordinary
Lyapunov exponent for a deep transformer). The apparent zeros were the answer, rendered invisible.

Worse than losing time: **that formatted zero was used as evidence.** "Layer 0 is bit-exact,
therefore reduction-order amplification is refuted" — an argument built entirely on a rounding
artifact, against the hypothesis that turned out to be right.

**What saved it was flagging on the RAW value, not the printed one:** `flag = "<<<" if of[0] > 0`.
That surfaced `n_diff=1` beside a row of zeros and forced a second look. Had the flag been computed
from the displayed number, the conclusion would have been confidently wrong.

**Rules that fall out:**

- **Print scientific notation for any quantity that can be small.** `%.3e` costs nothing and cannot
  round a signal to zero. Never `%.6f` for an error term.
- **Carry an integer count beside every float summary.** `n_diff` is immune to formatting: it said
  `1` while `max|d|` said `0.000000`. A count and a magnitude disagree only when the formatter is
  lying.
- **Compute thresholds and flags from raw values, never from what was printed.**
- **"Bit-exact" is a claim about bits.** If you did not compare bit patterns (or print full
  precision), say "agrees to the printed precision" — which is a much weaker statement and should
  never be used to refute a hypothesis.
- Corollary to [the noise-floor rule](#the-class-the-gates-own-noise-filter-manufactures-a-failure):
  a zero you cannot distinguish from a rounding artifact is not a zero.

## The class: a byte-exact gate is ROUTE-SCOPED — it proves losslessness on the path it ran

**"12/12 byte-identical" answers *this route agrees with itself*. It says nothing about a second
implementation of the same mathematics.** If a flag silently selects a different kernel, you can
hold every gate green on both settings while the two settings disagree with each other.

**2026-08-04.** `NOMOS_BATCHED_PREFILL` selects between square-batched prefill (a materialized
`[NH,S,S]` score matrix, `engine_prefill.mojo:145`) and a per-token path. Same model, same weights,
same mask — the sliding window is a MODEL property from the HF config, bounded to 1024 at
`:470`/`:619-622` regardless of any storage flag. Measured on the standard bar:

```
9 of 12 prompts produce DIFFERENT greedy tokens at ctx 54-216
  mem1@tok0   ag1@tok0   struct2@tok1   ag2@tok2   ag5@tok4 ...
```

Not a long-context edge case — 54 tokens. Not degeneracy: every arm is fully coherent (max_run=1,
distinct-2gram 0.96–1.00), so nothing looks wrong from the outside. And **square-batched is the
shipped default on discrete**, so this is the routine path.

Both routes are internally self-consistent, so **every lossless gate we own passes on either one.**
The gates were never wrong; they were answering a narrower question than we were reading them as
answering. This is [the self-referential-validation class](#the-class-self-referential-validation)
one level out: not "is my reference circular" but **"how much of the configuration space does my
green actually cover?"**

**Rules that fall out:**

- **State the route in the claim.** "12/12 lossless" → "12/12 lossless *on square-batched prefill,
  NVFP4 W4A4, block-32 INT4 KV*." A gate result without its route is an unlabelled measurement.
- **Any flag that selects a different KERNEL needs a cross-route identity gate**, not just a
  same-route regression gate. Selecting an implementation is not a tuning knob.
- **Print the resolved route, never infer it from the flag** (`NOMOS_ROUTE_BANNER=1`, and the engine
  banner's resolved attention geometry). Twice in one day a flag was read as determining behaviour
  it does not determine.
- **When two routes disagree, do NOT anoint one as ground truth because it is simpler or
  serialized.** A square GEMM and a per-token GEMV are both *legal reduction trees* over the same
  mask; there may be no uniquely correct floating-point association. Build a high-precision
  reference **from the identical quantized operands** and ask which is *closer*, then ask separately
  whether the difference *matters* (teacher-forced NLL / task quality). An external bf16 engine is a
  quality reference, not proof that a quantized route's token sequence must match it.
- **The discriminator between "rounding" and "defect" is margin vs delta, not the token.** A flip
  where the top1−top2 margin greatly exceeds `max|Δlogit|` cannot be reduction-order noise.

## The class: the GATE's own noise filter manufactures a failure

**A correctness gate compares two byte streams. If its filter lets nondeterministic bytes through,
it reports a kernel defect that does not exist — and the louder the gate, the more convincing the
lie.** This is the inverse of the trap above: there, a green gate hid a bad config; here, a red gate
invented a bad kernel.

**2026-08-04, the W=2 decode-GEMM arm.** A new arm (`NOMOS_FP4_QA_CFG=7`, W=2) was gated on
byte-exact token parity against the shipped W=4 arm before any timing was allowed to count. The gate
filtered engine chatter with `grep -vE '^\[|^$'` and ran `cmp`. It printed:

```
PARITY FAIL — W=2 changes output. STOPPING (a fast wrong kernel is worthless).
```

The kernel was fine. Both outputs were **449 bytes and character-identical**; what differed was a
tcmalloc warning that begins with a **digit**, not `[`, and carries a PID plus a **heap base
address**:

```
6399 external/tcmalloc/.../system_allocator.h:713] Warning: Unable to mbind memory ... base=0x3b8300000000
6433 external/tcmalloc/.../system_allocator.h:713] Warning: Unable to mbind memory ... base=0x39e700000000
```

An address that is *supposed* to vary run to run went straight into `cmp`. Filtering to model text
only: **292 bytes, sha `317f9f81…`, identical.** Parity passed.

**Rules that fall out:**

- **Diff the payload, never raw stdout.** Extract the generated text, then compare. Anything that
  can carry a PID, an address, a timestamp, a duration, or a device ordinal is not payload.
- **A parity FAIL deserves the same scepticism as a parity PASS.** The reflex on red is to believe
  it — it flatters the gate and it is the "safe" reading. Both directions are instrument output, and
  an instrument that can produce a false green can produce a false red.
- **Predict the expected result from source before running the gate.** Here the kernel splits
  **output columns** across warps (`grid_dim = N//(8*W)`, 8 columns per warp), so each dot product
  accumulates entirely within one warp and W cannot change the reduction order. Bit-identical was
  the *predicted* outcome, so FAIL contradicted a source-level derivation — that contradiction, not
  the diff, is what forced the re-read. **Without a prediction there is nothing for a bad instrument
  to contradict.**
- **A red gate that stops the run costs exactly as much as a wrong number.** This one was seconds
  from ending a live rental experiment on the box that was rented to answer the question.

## Benchmarking the reference engine — three traps from the 5090 head-to-head (Prime, 2026-08-04)

Comparing against llama.cpp is only meaningful if llama.cpp is actually doing what you think.
Three traps, each of which produced a plausible number:

1. **`--spec-type` defaults to `none` — and everything still looks fine.** Passing `-md` and
   `--spec-draft-n-max` loads the drafter, logs it, and parks it in VRAM **while you measure base
   decode.** The tell that caught it: the "speculative" number matched base *too exactly*
   (31.27 vs 31.45). The **only** trustworthy signal that speculation ran is `draft_n` in the
   per-response timings — `/props` reports `speculative.types = none` **even while speculation is
   active** (it reflects the default template, not the slot).
2. **Prompt choice moved the same binary 1.9×.** One Fibonacci prompt: 67.14 ("we are 25%
   behind"). Ad-hoc bucket-shaped prompts: 35.92 ("we are 49% ahead"). The frozen bar: 45.80
   (+16.8% ahead — the real answer). Never compare a weighted median against n=1; the frozen bar
   exists precisely so both stacks answer the same question. This is rule 1 wearing the
   reference engine's clothes.
3. **A build script that reports success on failure** (ours, not theirs): `refresh_build.sh`
   piped every command to `tail`, so `set -e` could not fire — it printed its own failure signal
   ("perf code present? 0") and exited 0 with the `.so` untouched. Fixed 2026-08-04
   (`set -eo pipefail` + a hard gate on the symbol count). Same family as the lossy-but-faster
   BLOCK_ATTN trap: the failure mode that *looks like* a pass is the one that costs a day.

## The class: self-referential validation

**A gate whose reference descends from the artifact it is gating measures consistency, not
correctness.** This has bitten this project three times in eight weeks, in three different
subsystems, and each one *felt like validation while it was being built*. That is what makes it
worth a name: the error is invisible from inside the gate.

| date | the gate | what it actually proved |
|---|---|---|
| 2026-06-08 | kernel-vs-kernel concordance | two implementations agree — not that either is right |
| 2026-08-02 | "12/12 lossless" spec-vs-base | spec matches base **on the same path**; both corrupted identically still passes |
| 2026-08-02 | R2 weight manifest, "834/834, 0 mismatch" | **transfer integrity only** — the manifest was generated *from* the source copy, so a wrong source would be faithfully certified onto every box that pulled it |

The manifest is the most dangerous of the three, because **nothing broke.** The other two were
found because something failed. `0 missing, 0 mismatch` would have gone on reading like
certification indefinitely.

**Ask of any green gate: where did the expected values come from, and could the artifact have
contaminated them?** For the manifest, the external referent would be a hash computed at
conversion time from the source safetensors — upstream of the copy being verified, not derived
from it.

**Corollary:** "0 mismatch" is not evidence of correctness when the reference descends from the
thing under test.

### The condition — when a self-referential gate is actually valid

> **A self-referential gate is valid precisely when the change under test cannot affect the
> referent, and blind when it can.**

That is a test you can apply *before* trusting a green result, rather than a caution you appreciate
afterwards. Ask: *does my change touch the thing I am comparing against?*

**The counter-example that proves the gates are conditional, not useless.**
`NOMOS_VERIFY_BLOCK_ATTN` routes verify attention from per-token fallback to a prefix-block path.
It is genuinely faster — +29% on Gold/int8, +7.4% on the 5090 bf16 champion, verify 49.90 → 46.29 ms.
It is also **an approximation, not an exact reformulation**: 3/12 lossless on int8, 1/12 on bf16,
with divergences as early as token 2. **The gate caught it, both times, on both cards.**

It caught it because the flag is gated on `start_pos > 0` — it perturbs only the **verify** path and
leaves base decode untouched, so the referent kept genuine external standing. Block-32 moved
**both** paths identically, which is exactly the condition under which the same gate goes blind.
Same gate, same codebase, opposite outcomes, and the condition above predicts which you get.

### The operational form: `E == 1.00` passes the lossless gate

The condition above has one concrete signature that showed up **three times in a single day**, and
it is now guarded in code.

**`E[tokens/cycle] == 1.00` means every draft was rejected at every depth** — the drafter
contributed nothing. **And the lossless gate passes**, because with zero acceptance the speculative
path degenerates to base decode one token at a time and matches base decode trivially. Throughput
lands near 40% of expected while the gate prints green.

| when | how it presented |
|---|---|
| block-32 KV written fp16, read fp32 | corrupt KV, drafter taps garbage, `12/12 PASS` |
| the same bug on the demo laptop | 46.58 → 17.55 tok/s, E 2.69 → 1.00, `12/12 PASS` |
| a `SPEC_VB` sweep whose anchor took a different drafter-load path | 42.39 → 17.56, E 1.00, `12/12 PASS` |

Each was initially read as a performance result. **`tools/dflash_spec_loop.py` now fails loudly
below E=1.05** and warns below 1.30, naming the usual causes (drafter failed to load, wrong
`DFLASH_DIR`, drafter/target mismatch, corrupt KV via `NOMOS_KV_I4_BLOCK` vs the read path).

**The general move: when a gate can be satisfied by the thing under test *disappearing*, put a
liveness check next to it.** Losslessness says the two streams agree. It never says the speculative
machinery did anything. Those are different claims and only one was being tested.

## A separate class: records that store only what worked

> **A record that stores only what worked is not a record, it is fiction.**

This is **not** a measurement error. It is a *record-keeping* error that manufactures measurement
errors downstream — at arbitrary delay, in someone else's hands. That is what makes it worth
separating from everything above: the other traps mislead you while you are looking at them; this
one lies dormant in a summary and misleads a colleague weeks later, carrying your authority.

`NOMOS_VERIFY_BLOCK_ATTN` failed its gate at 14:58 (3/12 lossless) and was correctly dropped from
the champion config. It was then written into a summary ladder as a contributing rung — the win
recorded, the refutation not. Six hours later that ladder was the source for telling a colleague it
was a "+6.5% measured" lead worth a fresh run: an experiment whose answer already existed in a bench
log on the same disk, with the opposite sign. It took his run to re-refute it.

It does not fail loudly. It fails by handing a confident false lead to someone who trusts you.

**Rules:**
- Search your own bench logs before proposing a "new" experiment.
- Write negative results down with the same care as the wins. A ladder is a record of what was
  *tried*, not a highlight reel of what survived.
- **A flag found shipping disabled is not automatically an unclaimed win.** It is equally likely a
  failed gate nobody recorded. Check whether it was correctness-gated when it was measured before
  treating it as free — "several unaudited flags" and "several unwired wins" call for opposite
  actions, and the evidence for both looks identical.
- **Verify-first, never enable-first.** A flag whose measurement was not lossless-gated is
  *unverified*, not a win. It goes back for re-measurement, not into a runner.

## The traps

### 1. `nm` on a JIT'd kernel — reports a missing kernel that is present

Mojo compiles GPU kernels through `compile_function`, so they are **never host symbols**.
`nm -D libnomos_kernel.so | grep <kernel>` returns nothing no matter how correct the build is.
Presented as: a freshly built `.so` looking like a broken build.
**Correct detector:** grep the source, or check the `nomos_` host-symbol count (85) for build health.

### 2. `nsys` inflates GPU time ~2.03× vs wall clock

Combined with `PERF_REPEAT=2` doubling instance counts, this made the lm-head look about half its
real share of the token and sent optimisation at the wrong target for hours.
**Correct detector:** wall-clock A/B on the end-to-end number. Use the profiler for *proportions
within one capture*, not for absolute cost.

### 3. A cross-arch constant printed as a baseline

`dflash_spec_loop.py` printed `(reference ~21.7)` — an NVFP4-discrete number — on every box.
GB10/Q4_0 base decode is ~10.2, so a perfectly healthy **10.73 read as a 2× regression**. It cost
an extra benchmark arm to chase, and the resolution was that the *constant* was wrong.
It hid because it validated on the box it came from: the same string read 21.75 on the laptop and
looked fine.
**Fixed:** the reference is now arch-selected and overridable with `NOMOS_BASE_REF`.

### 4. A debug reader that only understands one layout

`debug_kv_i4_source_to_f32_dev` decodes block-1 KV scales only. Under `NOMOS_KV_I4_BLOCK=32` it
will happily misreport rather than fail. It is opt-in behind `debug_omlp_stage==11`.
**Do not diagnose with it under block-32.**

### 5. The defect the instruments missed — an unconditional write with a partial reader

`gpu_append_quant_kv_i4` writes the block-32 scale layout **unconditionally**, as `float16` at
`[nkv, cache_cap, nblocks]`. Two readers existed and only one took `scale_blocks`; the other loaded
`Float32` from offset 0, **reinterpreting two fp16 scales as one fp32.**

Not coarse KV — garbage KV. No crash, no error, no failed assertion. The drafter tapped corrupt
hiddens and speculative acceptance went to **exactly 0.000 at every depth**.

Two things made it survive review:
- The reader is selected by `act_precision()==8`, i.e. an *activation-precision* flag, which has no
  obvious relationship to KV scale granularity. The measurement that showed +15.7% happened to set
  it; the default runner did not.
- Every gate we had was **self-referential**. "12/12 lossless" compares spec decode against base
  decode *on the same path*. When both are corrupted identically, a corrupted config passes its own
  lossless gate.

**The tell that cracked it:** acceptance of **exactly 0.000, not merely worse**. Degradation is
noisy; exact zeros at every depth are a systematic mismatch signature. Read the *shape* of a number,
not only its magnitude.

## The 2×2 that should have existed from the start

Two binary factors — read path × scale granularity — is four cells, and every edge answers exactly
one question:

```
                block=1     block=32
bf16 (DP4A=0)     A            B
int8 (DP4A=1)     D            C

A->B   does block-32 pay on the cheap verify path
D->C   does block-32 pay on the int8 path
A->D   what the int8 read path itself COSTS
B vs D the ship decision
```

Shipped first as three arms, missing D. The absence bit immediately: arm C returned a base-decode
figure with no same-path control, so there was no way to tell a hardware cost from a build
regression. Separately, a **diagonal** comparison (A vs C, moving both factors) had produced a
"+37% verify cost of the int8 path" that was really two effects summed; isolated, `A→D` is +35.2%
and the attribution is finally earned rather than assumed.

Harness: `tools/kv_block32_matrix.sh`. It prints the resolved weights path, drafter path, CUDA lib,
and whether the block-32 bf16 reader is present in the build — because a row measured on the wrong
model or a stale build is the same class of invisible error as everything above.

### What the completed matrix showed

Three cards, same harness, same pinned `SPEC_VB=5 / PERF_NTOK=64 / PERF_REPEAT=1`, base weights,
every discrete cell 12/12 lossless:

```
                  A bf16/blk1      B bf16/blk32     D int8/blk1      C int8/blk32
5090 laptop       46.94  E 2.67    51.40  E 2.92    40.13  E 2.86    43.62  E 3.07
Gold PRO 4000     37.64  E 2.28    42.39  E 2.76    26.11  E 2.20    34.28  E 2.68
GB10 (Q4_0)       degenerate*      degenerate*      18.89  E 2.84    18.22  E 2.87
```
\* on GB10, `DP4A=0` collapses acceptance to 0.000 for reasons unrelated to block-32; only the
int8 row is interpretable there. See the aarch64 warning in the harness.

**The read-path tax is card-invariant; the block-32 benefit is not.**

```
A->D  int8 read path, VERIFY cost    +35.2% (5090)   +32.3% (Gold)      <- fixed architectural tax
A->B  block-32 on bf16               +9.5%  (5090)   +12.6% (Gold)      <- variable
D->C  block-32 on int8               +8.7%  (5090)   +31.3% (Gold)   -3.5% (GB10)
```

This split was **predicted before the Gold data landed** — on the hypothesis that block-32 repairs a
KV-scale-granularity deficit, so its payoff tracks how much deficit a card carries, while the read
path is a fixed cost. Gold starts at bf16 acceptance 2.28 vs the 5090's 2.67 and recovers more than
twice as much. A prediction that survives is worth more than a fit that succeeds.

**Verify is flat-or-better under block-32 in all four discrete cells** (+1.1%, −0.1%, −3.0%, −3.2%),
which is the property that made decoupling it from `NOMOS_Q4_DP4A` worth building.

**Unexplained, flagged rather than storied:** the int8 read path *changes the sign* of its
acceptance effect between cards — 5090 `A→D` gains +0.19 E, Gold loses −0.08 E. That, not the verify
tax, is why the throughput penalties diverge (−14.5% vs −30.6%) while the verify taxes agree.

## The class: A FAILURE THAT MANUFACTURES A NUMBER

**2026-08-08, found on the public tree.** `nomos_dflash_load` returned **rc=0 — success — for a
directory that does not exist.** `load_f32_file_to_gpu` returns 0 on a missing file and `_dflash_f32`
only prints `[WARN]`, so the drafter was built entirely from null device pointers and the FFI
reported success. The caller's `assert rc == 0` passed. Every `nomos_decode_step` then raised
`CUDA_ERROR_ILLEGAL_ADDRESS`, the harness's timing loop divided by a near-zero interval, and it
**printed `21789.19 tok/s`** before aborting on a poisoned context.

**A crash loop produced a plausible-looking throughput number.** Not a wrong number — a number with
no referent at all, in the right units, in the right column, ~2000× off. Any harness that reads
tok/s without also checking rc and parity would have banked it.

**The general form: a failure that leaves the timing loop intact will always produce a number,
and the faster it fails the better that number looks.** Division by a near-zero interval is the
mechanism. This is the same family as the silent chunked-prefill truncation (`max_seq`, #74): the
run "succeeded", the instrument was intact, and only the meaning was gone.

**Defenses, in order of strength:**
1. **A loader must stat its inputs and refuse BY NAME.** `nomos_dflash_load` now probes
   `fc_weight.q4`/`.q8` and returns `-2` naming the path. Zero is not a failure signal.
2. **Sentinel, never zero** — poison the output buffer before the call and verify it changed
   (`nomos_prefill` does this). Zero cannot distinguish "not written" from "written zero".
3. **Never read a throughput without its correctness gate in the same glance.** Both entry points
   print `LOSSLESS n/12` beside the tok/s for exactly this reason.
4. **A number 2000× outside the physical envelope is not an outlier, it is a broken instrument.**
   GB10 decode is bandwidth-capped near 10–11 tok/s. 21789 was never reachable; nothing in the
   harness noticed, because nothing was asked to.

**And the meta-trap that cost two runs:** the drafter *did* warn — `[WARN] Empty or missing DFlash
f32:` — at load time, near the top of the log. My own `| tail -32` cut it off. **The instrument told
me and my filter hid the telling.** Same family as the parity-gate false FAIL where the filter
stripped the wrong line. When you filter a log, you are choosing what you are allowed to learn.

## The class: A GATE THAT NAMES WHAT IT FORBIDS

Building the export preflight (`tools/export_preflight.sh`) surfaced a structural property of
security gates worth stating once:

> **A blacklist gate can never be published. A whitelist gate always can.**

`tools/prose_gate.sh` is blacklist-based — its `PAT` regex enumerates the mechanism descriptors it
hunts — so publishing it would publish the list of things we consider proprietary. It is permanently
internal. `tools/nm_gate.sh` is whitelist-based and says so on its own line 3: *"lists what's ALLOWED,
never what's forbidden, so the gate itself stays clean."* It enumerates only the permitted export
surface of a public `.so`, discloses nothing, and ships today.

**Write gates as whitelists and they stop being secrets.**

**The sharper half, learned by getting it wrong within an hour of building the gate:** I added a rule
excluding `nm_gate.sh` by *assuming* "it's a gate, so it must name forbidden things", without reading
it. The gate then flagged the published repo, and I nearly escalated a non-issue as a live
disclosure. Reading the file retired the rule.

> **A gate that false-positives on an assumption loses the authority to stop anything — and that
> failure mode is more dangerous than the leak it guards against, because people start overriding it.**

A deny rule is a claim about the world. It gets the same evidence standard as any other claim:
inspect the file before you write the rule.
