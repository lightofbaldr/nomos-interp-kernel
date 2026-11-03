# Saved-turn inspection pages

`/statistics`, `/interpreter` and `/system` replace the three placeholder sidebar items. All retain the conversation query; Statistics and Interpreter also accept the saved readout request as `turn`. They use the existing Pi connection, not a second generation pipeline.

## Statistics

Counts refer to received, deduplicated consumed-position records. They are **not** output-token accounting: a natural-stop token can lack a readout, and reused prompt positions are not recaptured. Prompt, generated reasoning, answer and other channels remain separate. Terminal coverage is taken from the recorded summary; an unfinished turn remains explicitly partial.

The client retains the bridge's durable `entryId` for each readout turn. Live readout events need not also arrive as `pi.entry_appended`; the ID lets the prompt resolver select the earlier saved user entry immediately, without inventing an entry or waiting for a reload. A snapshot and live terminal retain the same ID.

Direction names and layer axes come from the selected turn's header. Means use defined raw projections only, including real zero values. Missing values, channels and layers are not zero-filled. The trajectory breaks across missing rows or position gaps. The conversation table does not pool across direction assets or steering settings, and does not estimate a steering effect.

Alerts reuse the existing evaluator and current local rules; those rules have not changed. Unknown workspace labels/capture are identified separately from the number of observed alerts. The defaults now select the reviewed SP1 emotion asset by ID (or an unambiguous named emotion set), never its position in the registry. Random-only assets cannot silently become the emotion default.

## Interpreter

This is another presentation of the existing annotator, not a new model service. It joins by durable readout request, with one-shot admission, explicit consent, the dedicated server key and non-model Pi entries unchanged. Opening, changing turn, navigating or reloading never annotates automatically.

A completed or failed saved attempt exposes its exact `payload_json` and SHA-256, unchanged. The readable view displays scalar fields from that payload, without recomputing the summary; raw means and prior-mean differences have separate columns and counts. Missing legacy fields say “Not supplied.” A prepared payload on a failed/pending attempt does not prove delivery. No rejected provider prose is retained.

## System

`GET /api/system` is same-origin loopback only, read-only and no-store. It reports a whitelisted kernel health subset and the **UI host** separately. Local `/proc/meminfo`, memory PSI, session-volume `statfs` and a fixed-argv `/usr/bin/nvidia-smi` query are bounded and cached for 10 seconds. The GPU query has a two-second timeout, 32 KiB output cap and no shell. No browser-supplied paths, commands or endpoints are accepted.

The kernel may be reached through a localhost SSH tunnel. Local hardware measurements are therefore never labeled as remote backend hardware. Backend GPU memory, temperature and power remain unavailable. No SSH, kernel modification or service control is performed.

Cache counters require the explicit “Read cache counters” action. There is no background polling, one process-wide request in flight, a 65-second admission cooldown even after failure, and a check for an active Pi run in this app. External clients are not visible to that check. The upstream engine may continue waiting for its lock after a five-second client timeout; the cooldown prevents a rapid queue of retries. A failed sample retains the last successful values and timestamp, not zeros. The reuse ratio is cumulative reused/prompt tokens for the kernel, not a per-conversation hit rate.

## Verification

Use Node 24.22.1. `tests/inspection` covers arithmetic, missing values, recorded steering, registry order, payload field attribution, and probe admission/parsing. `scripts/inspection-browser.mjs` runs against fake kernel and fake Meta endpoints with an isolated Pi database; it checks navigation, reload, exact payload, mobile overflow and failure states. `scripts/inspection-regression.mjs` exercises existing chat/workspace/annotator and loopback behavior without using a real kernel or paid API.

1440×900 and mobile screenshots are written under ignored `preview/checks/`. The task ledger is in `GATES.md` beside this document. Real validation uses existing saved turns and read-only health; it never triggers paid annotation.
