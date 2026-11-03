# Kernel contract for the Nomos Muse frontend

From Prime, 2026-10-14; updated 2026-10-15, when the KV session store merged into `main` and was turned on. For Codex, getting this frontend ready for the Nomos kernel. Adam and Prime will finish the UI once the interp kernel is ready.

Each section is tagged with its status:

- **LIVE**: existed then in the Nomos product kernel, a private repository. Its serve, extended with readouts and steering, now ships at this repository's root, in `pyhost/`.
- **PLANNED**: designed, not built yet. (Everything this document first marked PLANNED landed with the KV session store on 2026-10-15 and is now LIVE.)
- **OPEN**: will be designed together with the interp kernel. Build the adapter against it, but don't treat it as final.

This document sits beside [INTEGRATION.md](INTEGRATION.md) and does not replace it. Keep that file's rules: chat and telemetry are independent, missing data is never shown as zero, and reasoning is never faked.

---

## 1. Chat (LIVE)

**Server.** `pyhost/serve.py`, started with `pyhost/serve_nomos.sh --model muse up` at `http://<host>:8091`: that is the launcher's default and Adam's host env. (`serve.py` started directly defaults to `:8090`.) The env vars `NOMOS_HOST` and `NOMOS_PORT` change it.

**Endpoints:**
- `POST /v1/chat/completions`
- `GET /health` (it also reports the KV session store's state; see section 3)
- `GET /v1/models`

**Request body** (`ChatReq`):

| Field | Default | Notes |
| --- | --- | --- |
| `messages` | (required) | The whole conversation. See section 2. |
| `stream` | `false` | **Send `true`**, for the live display. See below. |
| `max_tokens` | 2048 | |
| `temperature` | 0.0 | |
| `top_p` | 1.0 | |
| `top_k` | 0 | |
| `repetition_penalty` | `null` | `null` means the model's default. |
| `model` | `null` | Optional. |
| `tools` | `null` | OpenAI format. |
| `tool_choice` | `null` | OpenAI format. |
| `enable_thinking` | `true` | Ignored for Muse. Its reasoning is always on. |

**Temperature.** The demo runs at **temperature 0**, which is Adam's decision of 2026-10-14. That is the only setting at which the serve runs DFlash speculative decoding, measured at about 2.8× the decode speed. Default the UI to 0. If a temperature control exists at all, it should say that anything above 0 turns speculation off.

**Stream format.** The server sends OpenAI `chat.completion.chunk` events as SSE `data:` lines. Each `choices[0].delta` carries either `reasoning_content` or `content`. The stream ends with `data: [DONE]`.

**Display.** Show reasoning and content separately. Reasoning may be collapsible, but it is never hidden from history and never made up.

**Why streaming.** For the live display. Exactness no longer depends on it: since Task 10b (LIVE 2026-10-15) the non-streaming reply's `reasoning_content` and `content` are the same strings the streamed deltas join to, so either kind of client can send its history back verbatim (section 2). Before 10b the non-streaming reply trimmed whitespace and joined reasoning blocks with `"\n"`.

**Split characters (fixed by Task 10b, LIVE).** Glimmer writes many emoji as two tokens, each holding part of the character's bytes. The serve holds a partial character back until it is complete, so streamed text is exact, emoji included, and history matches the cache. A `�` (U+FFFD) that still appears is real: either the model generated it, or a reply cut off by `max_tokens` ended mid-character. Keep it as received (section 2, rule 2); don't try to repair it in the UI.

---

## 2. History: the rules the KV session store depends on (LIVE; the store is on)

The store keeps each conversation's KV cache. On every request it reuses the longest run of token IDs, counted from the start of the prompt, that matches what it already holds. Everything after the first token that differs is prefilled again. At long context that costs minutes of GPU time. If the difference falls inside an old reply, the store also forks the conversation, which means rewriting all of it: about 8.5 GB at 128K tokens.

Adam's decision: the client sends the full context, with the reasoning, back on every turn. Muse's chat template renders each past reasoning as `<|start|>assistant to=self<|message|>…<|eom|>`. That is how the model saw it when it was generated, so a faithful history is also a perfect cache hit.

**Rules:**

1. **Send the whole conversation every turn.** Include every past assistant message with both of its fields:
   ```json
   {"role": "assistant", "reasoning_content": "<the streamed reasoning, exactly>", "content": "<the streamed answer, exactly>"}
   ```
2. **Byte-exact.** Each field is the concatenation of its streamed deltas. Do not trim, normalize whitespace or Unicode, re-render markdown, or keep a "cleaned" copy for history. Store the raw strings and send those back.
3. **Never rewrite earlier messages, and keep the system prompt identical every turn.** Anything that changes from turn to turn goes into the newest user message: the time, readout settings, attached file contents, retrieved context. A system prompt that changes every turn throws away the whole cache every turn.
   - **Always send your own system message.** If a request has no system message, Muse's template writes its own default one, and that includes today's date (`Current date: YYYY-MM-DD`). A conversation continued after local midnight then differs from the cache at about token 31, and the whole conversation is prefilled again. At long context that takes minutes.
   - Your own system message gets no date line. If the model should know the date or time, put it in the newest user message.
   - The default header also carries `Knowledge cutoff: 2026-01-04.`. Copy that line into your system message if you want to keep it.
4. **Stopped replies.** When the user stops a reply, keep the partial `reasoning_content` and `content` in history exactly as received. If you drop the stopped reply, the next request diverges right after the previous user message. Kept, a stop costs nothing extra at temperature 0: the serve ends the turn exactly where the stream stopped, so the next request reuses everything up to that point.
5. **Edit, regenerate, and switching between conversations are all fine.** Edits and regenerations fork by design. The store keeps parked conversations in RAM or on NVMe and restores them when you switch back.
6. **Tools.** Send assistant `tool_calls` and `tool` messages back as the server returned them. The server itself converts `arguments` given as JSON strings into objects.
7. **One generation at a time.** The engine serves one conversation at a time. Don't run background generations during a turn, such as auto-titles or suggestions. Each extra generation parks the chat's conversation, and it must be restored afterwards.

**Verified.** `tools/test_stream_roundtrip.py` runs real Muse generations and checks that a client following these rules re-renders the next prompt to exactly the token ids the cache holds. A real conversation on 2026-10-15 agreed: the follow-up to an 8,568-token conversation reused 8,631 tokens and prefilled only the 24 new ones. If a model ever produces a token split that re-encoding cannot reproduce, the fix is on the server side and these rules stay the same.

---

## 3. KV store state and counters (LIVE)

**Endpoint.** `GET /v1/kvstore/stats` on the same serve. It returns one JSON object with ten integer fields:

| Field | Meaning |
| --- | --- |
| `prepares` | requests the store handled |
| `prompt_tokens` | tokens in those requests' prompts |
| `reused_tokens` | prompt tokens served from the cache instead of being prefilled |
| `restores_ram`, `restores_nvme` | parked conversations restored from each tier |
| `forks` | conversations split off into a new branch |
| `ram_bytes`, `nvme_bytes` | **current** bytes held in each tier |
| `ram_evictions` | chunks freed from RAM under budget pressure (their copies stay on NVMe) |
| `nvme_evictions` | evictions made by the NVMe tier's disk budget |

**Running totals.** Every field except `ram_bytes` and `nvme_bytes` is a running total since the store started. To get one turn's numbers, read before the turn and after it, and subtract. A missed read then loses nothing.

**When to fetch.** Only between turns. The endpoint takes the engine lock:
- During a generation it waits up to 60 s, then returns `503 {"status": "busy"}`.
- Before the engine is up it returns `503 {"status": "starting"}`.

**Suggested display per turn:**
- the change in `reused_tokens` out of the change in `prompt_tokens`, e.g. "31,744 of 32,100 tokens reused";
- whether the conversation was restored from RAM or from NVMe;
- whether it forked.

These counters fill the "KV offload counters" panel that INTEGRATION.md leaves pending, and they come with real units.

**Store state.** `GET /health` carries `"kvstore": {"status": …, "dir": …}` from the moment the serve is up. `status` is `open` (with the store's directory in `dir`), `off`, `disabled`, `off (unsupported)`, `refused (…)` or `error (n)`. Show it once, in a status area. The counters above only mean something while it is `open`; in any other state, show the panel as unavailable, not as zeros.

---

## 4. Readouts (OPEN)

The readout stream from the Glimmer interp kernel does not exist yet. Adam and Prime will define it together with that kernel, and it will include the emotion readout. Build the readout side as its own adapter behind the UI's frame model, so it can be swapped later.

**Closest working source today: the Muse Observatory backend** (research code that runs on Glimmer):
- **Code:** `~/Projects/honesty-probe/scripts/serve_muse_observatory.py`.
- **Address:** `http://<laptop>:8091`.
- **How to run it:** `bash tools/dflash_muse.sh scripts/serve_muse_observatory.py` from `honesty-probe`.
- **Request:** `POST /v1/interp-chat` with body `{messages, max_tokens = 1024, temperature = 0.0, top_p = 1.0}`.
- **Response:** SSE with named events, in this order:
  - `meta`, once:
    ```json
    {"schema": "nomos.interp.v1", "model": "…", "prompt_tokens": 0, "layers": [], "concept_layers": [], "concept_top_k": 0, "ignition_threshold": 0.0, "artifact_id": "…"}
    ```
  - `token`, one per generated token, with the SSE `id` set to its index:
    ```json
    {"request_id": null, "index": 0,
     "token": {"id": 0, "piece": "…", "text_delta": "…", "phase": "…", "visible": true, "position": 0},
     "readout_query_position": 0,
     "layers": {"<layer>": {"concepts": [{"label": "…", "score": 0.0}], "ignition": 0.0}},
     "depth": [{"layer": 0, "concepts": [{"label": "…", "score": 0.0}], "ignition": 0.0}]}
    ```
  - `done`, once: `{"tokens": N, "log": "<file>"}`.
  - `error`, instead of the events above if the run fails: `{"message": "…"}`.

**Its limits:**
- It generates the reply greedily, then re-captures all 52 layers and decodes them, and only then streams the events. **The events arrive after the reply is generated, not live.**
- It loads its own copy of Glimmer. It cannot run beside the chat serve on one laptop GPU (section 5).
- Its concept `score` values are on the Jacobian lens's own scale. They are not the cosine that `nomos.inspection.v1` declares. Don't relabel them as cosine.
- It has no emotion readout.

**Mapping it into `nomos.inspection.v1`:**
- **Token index:** `token.index` (or `token.position`) becomes `frames[].tokenIndex`.
- **Text offset:** `frames[].textOffset` is in UTF-16 code units, while the server counts Unicode code points. Compute the offset in JS from the accumulated `text_delta` strings, never from the server's lengths.
- **Concept scores:** `layers[L].concepts` holds a top-k list for each layer. A concept that is missing from a layer's top-k has no score at that layer. That is *unknown*, not zero (INTEGRATION.md's rule).

---

## 5. Running on one GPU

The chat serve (`:8091` under Adam's launcher) and the Observatory backend each load Glimmer. **Port clash to settle:** the Observatory script also listens on `:8091` (section 4). They can't run at the same time on the laptop anyway, but the UI should not have to guess which one answered: give the Observatory its own port when it is wired in. The laptop GPU holds one Glimmer at a time, so the UI must handle either backend being absent. INTEGRATION.md's "disconnected" state already covers this.

The GWV data layer (`:8130`, `~/Projects/global-workspaces-viz`) was built for Gemma-4-31B. Its SAEs and layer bands do not apply to Glimmer.

---

## 6. Map of the old code

| Where | What |
| --- | --- |
| `~/Projects/honesty-probe/scripts/serve_muse_observatory.py`, `observatory/viewer_muse.html` | The Muse Observatory, which runs on Glimmer (section 4) |
| `~/Projects/honesty-probe/scripts/observatory_glimmer.py` | The decision-point workspace readout from the 2026-09-18 study (CLI, not served) |
| `~/Projects/nomos-interp/pyhost/serve.py`, `viewer.html`, `gwv_capture.py` | The original `/v1/interp-chat` serve, viewer and GWV capture (Gemma-4-31B era) |
| `~/Projects/global-workspaces-viz/` | GWV: TimescaleDB with a FastAPI service on `:8130`, a WebSocket hub, and an SAE overlay. See its `STATE.md`. |
| `~/Projects/nomos-logos-site/src/lib/Observatory.svelte` | The original Observatory component from the website |

**Stack:** Svelte 5, Tailwind and bits-ui. No jQuery, no Bootstrap. Rebuild pieces of the old standalone HTML viewers as Svelte components instead of embedding them.

**Questions about the OPEN parts:** ask Adam, who will bring Prime in.
