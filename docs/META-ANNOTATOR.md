# Opt-in Muse Wonder second opinion

After a completed live Pi turn, **Ask Muse Wonder to read these signals** sends
that turn's text and a deterministic, bounded readout summary to Meta. Merely
opening a conversation, receiving readouts or reloading the page sends nothing.
The consent text is adjacent to the button. Notes are always labelled as a
second model's interpretation, not a verdict or a lie detector.

## Server configuration and scope

The single-user, loopback and same-origin restrictions in [LIVE-PI.md](LIVE-PI.md)
still apply. The server reads only `META_API_HACKATHON` from `~/.env` at runtime.
This is Adam's corrected spelling; the former `META_API_HACKATOHON` is **not** a
fallback. Do not source the file into the browser environment or commit it.
Missing/unreadable keys disable the button without affecting chat or saved notes.
An operator can override the file path with `GLIMMER_META_ENV_FILE`; tests use a
temporary file containing a dummy key. It is not a browser setting.

The only provider URL is `https://api.meta.ai/v1/chat/completions`; the model is
`muse-wonder-1.0`. This uses Meta's [Chat Completions
protocol](https://dev.meta.ai/docs/protocols/chat-completions), non-streaming, low
reasoning effort and a 2,048 completion-token budget. Redirects are refused. The
request is limited to 64 KB, the response to 128 KB, and the call to 60 seconds.
There are no automatic retries, including after an interrupted process. Provider
errors are replaced by fixed messages, never forwarded or logged. The key is
neither part of the stored payload nor returned to the browser.

## What the summary means

The browser sends only the conversation/request IDs, explicit consent and
validated page-local alert settings. The server finds the authoritative saved
readout turn and its completed assistant message by Pi task identity, then derives:

- The prompt, reasoning and answer as escaped text. Their character limits are
  4,000 / 6,000 / 12,000 respectively. Overlong text keeps its head and tail with
  a truncation marker, original length and selection rule. A length stop remains
  explicit. It does not send the full conversation or raw readout arrays.
- The **recorded turn header's steering**, including direction, signed strength,
  layer, asset and onset, or explicit none/unknown. Current UI controls do not
  replace the historical setting.
- At each configured emotion layer, up to four largest absolute **mean signed
  differences** per reasoning/answer channel. Every row is compared with the
  running mean of earlier defined generated reasoning rows of the same asset,
  name and layer, including earlier rows of this turn. The current row enters
  that mean after its comparison; answers never update it. This is the alerts'
  existing baseline, with 32 earlier values required, not a frozen pre-turn mean.
  Defined, missing, warm-up and omitted-direction counts remain explicit.
- The existing watch-list groups over generated **reasoning** positions: first
  matched token and position, rank, number of re-entries and last entry position.
  Groups follow prefixes, so later re-entries can be a different token sharing
  the same prefix. Unknown gaps do not count as absences. The rule settings are
  snapshotted at the click; changing them later does not rewrite a note.
- The eight most frequent L44 top-k token IDs in each generated reasoning/answer
  channel, with row counts, isolated-token labels when available, and missing
  coverage. These are top-k membership counts, not word counts or probabilities.
  Labels require the pinned local tokenizer and matching saved asset manifests.

No baseline is silently zero-filled. No lens or model inference is re-run to make
this summary. Projections are direction alignments, not emotional states; token
lists are approximate J-lens interpretations, not thoughts or proof of knowledge.
The local history limit is 250,000 recorded positions per annotation.

## Persistence and spend control

Admission and a `glimmer.meta.annotation` pending entry are committed atomically
in the conversation. A durable claim allows only one attempt per request. A
60-second per-conversation cooldown applies across different requests, with at
most 1,000 claims in a conversation. Concurrent duplicate clicks reuse the
existing pending/result entry. Failure is retained; there is no retry button.
An interrupted pending entry remains pending and is never automatically resent.

Completion stores the exact API request JSON, its SHA-256, requested model ID,
timestamps and note or safe failure. The UI exposes the payload. Pending/failed
attempts call it a *prepared* payload because network receipt cannot be attested.
These are non-model Pi entries: they have no model contribution and never enter
Glimmer's context. Reopening the conversation renders the saved note without a
new call, even when the key has subsequently become unavailable.

The system prompt limits the note to 120 words, names the signal scope, forbids
verdicts/causal/feeling/intent claims and permits at most one practical suggestion.
Length, response structure and a small set of explicitly forbidden claim words
are checked locally. That lexical check is **not** a semantic guarantee or a
fact-checker; viewers must compare the interpretation with the visible payload.
Model-produced notes remain escaped plain text, not trusted markup.

## Tests

After `npm run verify`, run `npm run test:meta`. This uses a fake Meta transport
loaded only in the test server process; production has no endpoint override.
It exercises real Pi storage, non-contamination of model context, durable
duplicates/cooldown, timeouts, secret-safe errors, missing keys, browser consent,
payload disclosure and reload. The shared browser harness defaults to a missing
key file so ordinary UI tests cannot read a real credential.

Paid validation is separate and manual, never part of the test command. The
initial acceptance limit is three one-shot calls, one per approved example.
