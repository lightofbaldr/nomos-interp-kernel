# Harness² on Pi, with Claude Code / Codex as improver and judge

Date: 2026-10-20. Status: approved by Adam on 2026-10-20 (all defaults and section 6 calls accepted). Repo: `~/Projects/harness2`, branch `pi-substrate`
(a clone of google-research/harness2 at 1cfe254).

## 1. Intent

Harness² optimises the *harness* around a fixed model: it rolls a task out under the current harness,
has a stronger "improver" model propose, probe and compose edits to that harness from the rollout
evidence, keeps the winner in a per-domain library, and measures the chosen rollout with each
benchmark's own judge. Upstream, every one of those three roles is wired to Google Cloud: the
executor runs inside OpenCode or Codex against Vertex Gemini, the improver runs through OpenCode (or
Claude Code on Vertex), and the judges call Vertex endpoints.

This project re-wires the three roles to what is installed on this machine, without changing the
method:

| Role (upstream name) | What it does | After this project |
|---|---|---|
| Executor / solver ("substrate") | Runs the benchmark task as an agent, under the harness being optimised | **Pi 1.3.0** running **GLM-5.3-flash** (default `zai/glm-5.3-flash`) |
| Improver / proposer / selector / reflector ("channel") | Agentic sessions that read the evidence tree and edit `context/harness/` in place | **Claude Code** (`claude -p`) or **Codex** (`codex exec`), subscription auth, no API keys |
| Judge | The benchmark's upstream grader (JobBench's `eval/judge.py`, LAB's `evaluation.run_eval`) | The same upstream graders, pointed at a **local OpenAI-compatible bridge backed by `claude -p` or `codex exec`** |

The point is to run the real Harness² loop (benchmarks, library, judging) on a cheap, fast executor
while the expensive thinking happens on models Adam already pays for by subscription.

Adam's stated goal is to improve real-world, day-to-day work with Pi, not benchmark numbers for
their own sake. The benchmarks are the yardstick that makes harness changes measurable; the
harnesses and library entries they produce are meant to flow back into daily use. That makes the
obvious follow-up, after this project, a "my own tasks" benchmark adapter (a directory of real
tasks with rubrics) and a way to export an adopted harness into a normal Pi project. Neither is in
this project's scope, but nothing here should make them harder.

The why, in Adam's words: "adapt harness2 so that it works with local Pi agent and uses Claude Code
or Codex for the judge and large model. Pi is going to be running GLM5.3 flash." Everything beyond
that sentence in this document is my reading of the code and this machine, and is marked as such
where it is a choice.

## 2. Goals, non-goals, anti-goals

Goals

- `harness2 --bench jb --substrate pi --k 1 --mode parallel` runs end to end on this machine:
  setup, check, sweep, judge, report. JobBench first (no extra system packages; 65 tasks across
  35 occupations, 0.41 GB download).
- LAB works the same way once `pandoc` is installed (the only missing system dependency; poppler is
  present). LAB is in scope but second.
- The improver channel is selectable between Claude Code and Codex with one `.env` line, and the
  judge backend likewise. Both CLIs are already installed and logged in.
- Runs are reproducible and isolated: a Pi rollout sees only the harness under test, never Adam's
  personal Pi packages, extensions, skills, MCP servers, memories or context files.
- Every new path keeps Harness²'s measurement contract: a judge or runner failure is `None`, never
  a zero, and never cached as complete.
- The unmodified upstream paths (OpenCode, Codex-on-Vertex, Vertex judges) keep working for anyone
  who has them; nothing is deleted, only made conditional.

Non-goals (v1)

- WorkBuddy-Bench. It needs Docker, which is not installed, and its judge runs inside Harbor
  containers. `--bench wb --substrate pi` fails with a clear message.
- Pi subagents (component c5). Pi core has no subagent tool; `pi-subagents` is a third-party package
  that would have to be loaded into the isolated runtime with its own model wiring. The surface keeps
  the `sub_agents/` path so prompts and the library stay coherent, validation rejects any that a
  proposal adds, and the prompt says spawning is unavailable (the Codex surface already does this).
- Image attachments to the judge on the Claude backend. JobBench's judge attaches images for tasks
  with image deliverables; the Codex backend passes them through (`codex exec -i`), the Claude
  backend refuses such requests so the task stays *unscored* rather than graded blind (4.4).
  Claude image input (`--input-format stream-json`) is a follow-up.
- Streaming responses from the bridge, and the Vertex/Gemini configurations (kept, not tested here).
- A "my own tasks" benchmark adapter. Not asked for; noted as the obvious next step if the real goal
  is improving Adam's daily Pi setup rather than benchmark numbers.

Anti-goals (what would make this a failure even if it technically works)

- Leakage from Adam's Pi configuration into rollouts (pi-lens, hermes memory, intercom, scheduler,
  MCP servers, the `agent-skills` skills path, `~/.pi/agent/AGENTS.md` if one ever appears). This is
  why the executor runs under its own agent directory *and* with every discovery flag off.
- Scoring an outage as a regression: a dead Pi process, a bridge timeout or a parse failure must
  surface as "unscored", exactly as the OpenCode and Codex paths do today.
- Breaking the demo (`harness2-demo`) or `tests/test_workflow.py`, which run with no model and are
  the regression gate for the method itself.
- Copying credentials. The isolated Pi agent directory *links* to Adam's `models.json`/`auth.json`;
  the bridge key is generated locally; nothing writes a provider key into the repo or `runs/`.

## 3. Constraints and facts about this machine (verified 2026-10-20)

- Pi 1.3.0 at `~/.local/bin/pi` (managed install; package docs under
  `~/.pi/agent/install/releases/1.3.0/node_modules/@earendil-works/pi-coding-agent/docs/`).
  Node v22.23.3 (Pi needs ≥ 22.19; extensions run through jiti, so TypeScript needs no build).
- Adam's Pi config: default model `zai/glm-5.2`, but the most recent session used
  `zai/glm-5.3-flash`, and `pi --list-models glm` lists `zai glm-5.3-flash` (1M context, vision).
  `models.json` also defines a local `spark-glm/glm-5.3-flash-redhat-nvfp4` on <private-lan-host>:8010,
  which was unreachable at the time of writing. Default solver: `zai/glm-5.3-flash`; the local one is
  a one-line `.env` change.
- Adam's `settings.json` loads 18 packages (pi-lens, pi-hermes-memory, pi-intercom, pi-scheduler,
  pi-subagents, superpowers, …) and a skills directory. All of that must stay out of rollouts.
- Claude Code 2.1.304, logged in (subscription). Headless flags available: `-p`, `--output-format
  json`, `--bare` (skips hooks, plugins, CLAUDE.md discovery, auto-memory), `--model`, `--effort`,
  `--tools ""`, `--json-schema`, `--max-budget-usd`, `--append-system-prompt`,
  `--dangerously-skip-permissions`. Note: when harness2 itself is launched from inside a Claude Code
  session, the environment carries `CLAUDECODE`, `CLAUDE_CODE_*` variables; the improver subprocess
  must strip them so the nested `claude -p` behaves like a fresh one. **`--bare` is not usable
  here**: on 2.1.304 its help says auth is "strictly ANTHROPIC_API_KEY or apiKeyHelper … (OAuth and
  keychain are never read)", so a `--bare` session would not see the subscription login. Isolation
  for the Claude roles therefore comes from the narrower flags `--setting-sources`,
  `--strict-mcp-config` and `--disable-slash-commands` (4.3), which the build verifies first.
- Codex 0.163.0, "Logged in using ChatGPT". `codex exec` supports `--json`, `-o <file>` (last
  message), `--output-schema <file>`, `--ephemeral`, `--skip-git-repo-check`, `--ignore-user-config`,
  `-s read-only|workspace-write|danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`,
  `-c model_reasoning_effort="high"`, prompt from stdin when the positional prompt is `-`.
- Not installed: OpenCode, Docker, pandoc, gcloud, podman (so LAB's document parser runs in host
  mode), `gh` login. `bun` exists but is irrelevant once OpenCode is not needed. No user-level
  `~/.claude/CLAUDE.md` or `~/.pi/agent/AGENTS.md` exists. 73 GB free.
- Upstream judges: JobBench's `eval/judge.py` is an OpenAI **chat-completions** client taking
  `--api-base` and `JUDGE_API_KEY` (plus the harness2 patch's `reasoning_effort` extra_body). LAB's
  `evaluation/judge.py` picks its SDK from the model-name prefix (`claude*`, `gemini*`, `gpt*`,
  `mistral*`) and its OpenAI path uses the **Responses API** (`client.responses.create(input=…,
  text.format=json_schema)`); harness2 already patches this file (`vertex/` prefix), and this project
  extends that patch.

## 4. Design

### 4.1 Settings (`.env`)

The repo claims a `.env.example` it does not ship; this project adds one. New or changed keys:

```
# executor (Pi)
HARNESS2_SOLVER_MODEL=zai/glm-5.3-flash      # Pi provider/model; unchanged key, same syntax as OpenCode
HARNESS2_PI_THINKING=high                    # pi --thinking; off|minimal|low|medium|high|xhigh|max
HARNESS2_PI_TOOLS=+grep,+find,+ls              # additive form: a plain allowlist drops extension-registered tools (verified 2026-10-20)
HARNESS2_PI_BIN=pi                           # or an absolute path
HARNESS2_PI_CONCURRENCY=2                    # process-wide cap on concurrent Pi rollouts

# improver (proposer, composer, selector, reflector)
HARNESS2_IMPROVER_CHANNEL=claude_cli         # claude_cli | codex_cli | opencode (upstream default)
HARNESS2_IMPROVER_MODEL=opus                 # bare id/alias for the CLI channels; provider/model for opencode
HARNESS2_IMPROVER_EFFORT=high                # claude --effort / codex model_reasoning_effort
HARNESS2_CLAUDE_USE_VERTEX=0                 # 1 restores upstream's Vertex env for claude_cli
HARNESS2_LIMIT_MAX_WAIT=7200                 # seconds to wait out a plan usage limit before giving up
HARNESS2_STOP_ON_LIMIT=0                     # 1 stops the sweep cleanly on a usage limit instead of falling back

# judge: the upstream graders talk to the local bridge
HARNESS2_JUDGE_MODEL=cli/claude:sonnet       # default changed after the first live run: Codex is rate-limited under parallel judge calls (docs/PI.md)
HARNESS2_JUDGE_BASE_URL=http://127.0.0.1:<port>/v1   # printed by harness2-judge-bridge
HARNESS2_JUDGE_API_KEY=<generated>                   # printed by harness2-judge-bridge
HARNESS2_JUDGE_EFFORT=low                    # upstream used "low" for its Gemini judge; passed through
HARNESS2_JUDGE_WORKERS=6                     # JobBench judge --max-workers; keep <= the bridge's --concurrency
```

My defaults, and why: improver on Claude Code (`opus`, high effort) because the `claude_cli` channel
already exists upstream and `--bare` gives a clean session; judge on Codex so the model that grades
is not the model that wrote the harness. Adam can flip either; the design works with any
combination, including both on one CLI.

`config.py` gains: `SOLVER_MODEL_PI` (= `HARNESS2_SOLVER_MODEL`), `PI_BIN`, `PI_THINKING`,
`PI_TOOLS`, `PI_AGENT_DIR = DEPENDENCIES/"pi"/"agent"`, `IMPROVER_CHANNEL`, `IMPROVER_EFFORT`,
`JUDGE_EFFORT`, `CLAUDE_USE_VERTEX`, and `needs_opencode()` = substrate is `oc` **or** improver
channel is `opencode`. `validate_models` requires `provider/model` syntax for the solver on every
substrate (Pi uses the same form) and for the improver only on the `opencode` channel. The
`judge_endpoint`/`executor` entries in the run identity (`run_sweep._check_identity`) use the Pi
model and the bridge URL on this substrate; the bridge reuses its saved port so the identity is
stable across restarts.

### 4.2 The Pi substrate (`harness2/substrate/pi.py`, name `pi`)

**Surface.** `PI_SURFACE = SurfaceSpec(name="pi", …)` maps the eight components onto Pi:

| harness path (editable) | component | how Pi gets it |
|---|---|---|
| `systemprompt.md`, `guardrails.md`, `LongTermMEMORY.md`, `tool_descriptions/tool_guidance.md` | c1, c2, c4, c3 always-on | composed into one file passed with `--append-system-prompt` (omitted when the composition is empty) |
| `skills/<name>/SKILL.md` (+ `skills/<name>/scripts/*`) | c6 | rendered to `.pi/skills/<name>/`, each loaded explicitly with `--skill <dir>`; Pi advertises name+description in the system prompt and the model reads SKILL.md on demand |
| `scripts/<name>.py` | c7 | copied to `<workspace>/scripts/`, listed in the always-on composition, run via bash |
| `extensions/<name>.ts` | c8 | rendered to `.pi/extensions/<name>.ts`, each loaded explicitly with `-e <file>`; the in-loop lever (`tool_call` can block or mutate, `tool_result` can rewrite, `registerTool` adds a tool) |
| `sub_agents/<name>/AGENT.md` | c5 | path exists for library coherence; `allow_subagents=False` on every pi cell, so validation rejects it |

Settings on the spec: same four always-on files and 24 000-char cap as OpenCode (so JobBench's
existing composition code and library entries carry over), `skill_dir="skills"`,
`skill_name_rule="must_match"` (Pi tolerates mismatches, but the portable form is the one the
Agent Skills spec and the other substrates require), `autotrigger_file="guardrails.md"`,
`trigger_line="- When {desc}, read the `{name}` skill's SKILL.md and follow it.\n"`,
`plugin_dir="extensions"`, `hooks_file=None`, `compile_skill_scripts=True`,
`reject_empty_always_on=False` (the base harness *is* bare Pi), and README stubs for `scripts/`,
`skills/`, `extensions/` and `sub_agents/` (the last one says "not available on this substrate").

**Install mode** `pi_workspace` (added to `INSTALL_MODES`). `materialise()` copies the tree to a
staging dir and renders `_rendered/` beside it:

```
_rendered/
  APPEND_SYSTEM.md          c1 + "# Operating Guardrails" + c2 + c4 + "# Long-Term Memory" + c3
                            + "# Helper scripts" listing of scripts/*.py   (same composition and
                            {{token}} interpolation as jobbench.compose_agent_body; HTML comments
                            stripped so an empty component renders to nothing)
  .pi/skills/<name>/...     whole skill directories
  .pi/extensions/<name>.ts
  scripts/<name>.py
```

The composition helper moves out of `jobbench.py` into the substrate module (JobBench keeps calling
it), so LAB-on-pi and JobBench-on-pi render always-on text the same way. Render context tokens
(`env_description`, `domain_focus`, `date`, `working_directory`) stay benchmark-supplied.

**Invocation.** The reference `pi_argv()`/`pi_env()` (benchmark launchers build their own, as the
other substrates do):

```
timeout --kill-after=30s <T> \
pi --mode json --session-dir <run>/pi_sessions --name <label> \
   --model <provider/model> --thinking <level> --tools <HARNESS2_PI_TOOLS> \
   --no-extensions --no-skills --no-prompt-templates --no-themes --no-mcp \
   --no-context-files --no-approve --offline \
   [--append-system-prompt <ws>/.harness2/APPEND_SYSTEM.md] \
   [--skill <ws>/.pi/skills/<name>]... [-e <ws>/.pi/extensions/<name>.ts]... \
   -- "<task prompt>"
env: PI_CODING_AGENT_DIR=<repo>/dependencies/pi/agent  PI_SKIP_VERSION_CHECK=1  PI_TELEMETRY=0
cwd: <workspace>; stdout -> <run>/*.jsonl (JSON events); stderr -> <run>/*.stderr.log; stdin: /dev/null
```

Why each flag: `--mode json` is the one-shot mode that streams the authoritative event record
(`message_end` messages, `tool_execution_end` results, retry and compaction events); `--no-*`
switch off every discovery path (Pi docs: `--no-extensions` disables discovered, configured and
built-in extensions; explicit `-e`/`--skill` still load); `--no-context-files` stops `AGENTS.md`/
`CLAUDE.md` discovery up the directory tree, which runs regardless of trust; `--no-approve` makes the
project-trust decision deterministic even though nothing is discovered; `--offline` suppresses
startup catalog refresh and version checks; `--session-dir` keeps the session file next to the run
for `pi --export` and for the LAB continue-nudge (below). The prompt is a positional argument (the
JobBench prompt is ~1 KB, LAB instructions well under the 128 KB per-argument limit).

**Isolated agent directory** `dependencies/pi/agent/`, created by setup:

- `settings.json`: `{"defaultProjectTrust": "never", "quietStartup": true, "packages": [],
  "extensions": ["-builtin:mcp", "-builtin:llama.cpp", "-builtin:codemode", "-builtin:tool-search"]}`.
- `models.json` and `auth.json`: symlinks to the files in `~/.pi/agent/` (or to
  `HARNESS2_PI_MODELS_JSON`/`HARNESS2_PI_AUTH_JSON` when set). Pi's credential order is
  `--api-key`, `auth.json`, `models.json` `apiKey`, then provider env vars, so Adam's zai key is
  found through the link and never copied.
- Setup then runs `pi update --models` under this directory so the catalog cache exists, and
  preflight runs `pi --offline --list-models <model>` under the same environment and requires
  exactly one row for the configured provider/model.

**Concurrency.** `PiSubstrate.execute()` takes a process-wide semaphore of
`HARNESS2_PI_CONCURRENCY` (default 2) before launching, so `--workers N` times the method's
`PHASE_WIDTH` of 2 can never exceed the zai plan's tolerance without an explicit decision. Inside a
rollout, Pi's own agent-level retry (3 attempts, exponential backoff, visible as `auto_retry_*`
events) handles transient provider errors, and the runner's attempt loop (`MAX_RETRIES=2`, as the
OpenCode runner) handles a dead process; a rollout that still produces nothing stays unmarked.

**Trajectory.** `read_trajectory()` renders the JSON event stream into the shared grammar:
assistant `message_end` → `[THINKING]` (thinking blocks), `[ASSISTANT]` (text), `[ACTION]
<tool>(<args clipped to args_trunc>)` (toolCall blocks); `tool_execution_end` → `[OBSERVATION]` or
`[OBSERVATION ERROR]` with the result's text content clipped to `obs_trunc`; user `message_end` →
`[USER]`; `auto_retry_start`/`auto_retry_end{success:false}` → `[ERROR]`; `compaction_*` →
`[HARNESS] compaction …`; `agent_settled{aborted:true}` → `[ERROR] aborted`. `toolResult` role
messages are not rendered (the observation comes from `tool_execution_end`); the builder confirms
against a real stream that this does not drop or double anything. A side function sums usage from
assistant `message_end.usage` (input, output, reasoning, `cost.total`) and counts `turn_end` and
`tool_execution_end` for metrics. `final_message()` returns the last assistant text.

**Preflight.** `pi` resolvable and ≥ 1.3; node ≥ 22.19; agent dir present with both links resolving;
model listed (above); never starts a model call.

### 4.3 Improver channels (`core/spec.py`, `core/proposer.py`)

`CHANNELS = ("claude_cli", "codex_cli", "opencode")`. `resolve_channel_model` treats `codex_cli` like
`claude_cli` (bare model id, no `/`). `config.DEFAULT_PROPOSER_CHANNEL` comes from
`HARNESS2_IMPROVER_CHANNEL`; `PROPOSER_MODEL_BY_CHANNEL` gains the codex entry; the adapters'
`_channels()` pass `effort=config.IMPROVER_EFFORT`.

`Proposer.env()`: inherit the environment, strip `CLAUDECODE` and every `CLAUDE_CODE_*` variable
(nested-session markers), and apply the Vertex block (`CLAUDE_CODE_USE_VERTEX`,
`ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, `ANTHROPIC_MODEL`, and the `_DROP_ENV` key removal)
only when `HARNESS2_CLAUDE_USE_VERTEX=1`. Upstream dropped `ANTHROPIC_API_KEY` unconditionally; here
it is left alone unless Vertex is selected, because a user who sets it has chosen to be billed that
way.

`_claude_cli`: `claude -p --output-format json --dangerously-skip-permissions --model <m>
--effort <e> --setting-sources "" --strict-mcp-config --disable-slash-commands
[--append-system-prompt …]`, mission on stdin, cwd = the session workspace (which holds `context/`).
`--setting-sources ""` is meant to load no settings file (so none of Adam's hooks or enabled plugins
run), `--strict-mcp-config` with no `--mcp-config` connects no MCP server, and
`--disable-slash-commands` keeps every skill and command out of context. The first build task is to
prove this isolation empirically (a temporary user-level `SessionStart` hook that writes a sentinel
file must not fire; the session must still authenticate with the OAuth login; `CLAUDE_CODE_SIMPLE=1`
is tried as an alternative, and `--bare` is used only when `ANTHROPIC_API_KEY` is set). If no
flag combination both isolates and authenticates, the default improver and judge backend become
Codex and the Claude channel is documented as API-key only. Result parsing is unchanged (`result`,
`total_cost_usd`, `is_error`/`subtype`).

`_codex_cli`: `codex exec --ephemeral --skip-git-repo-check --ignore-user-config
--dangerously-bypass-approvals-and-sandbox --json -C <cwd> -m <m> -c model_reasoning_effort="<e>"
-o <cwd>/.harness2_last_message.txt -`, mission on stdin with any `system` text prepended as a
`# SYSTEM INSTRUCTIONS … # END` block (Codex has no system-prompt flag). The reply text is the
`-o` file (falling back to the last `agent_message` item in the JSONL); `cost_usd` is 0.0 because
Codex reports tokens, not dollars, and the token counts from `turn.completed` are recorded in the
audit `meta.json`. `--ignore-user-config` keeps Adam's `~/.codex/config.toml` out while auth still
comes from `CODEX_HOME`. Full access is required because the session's job is to edit
`context/harness/` in place.

**Plan usage limits.** Both subscriptions have 5-hour and weekly caps that a 65-task sweep will
hit. Each channel classifies a failed call: a usage-limit or rate-limit signal (Claude's result
`is_error` text / Codex's error item naming a limit, reset time or 429) is **waited out** inside
`Proposer.call` before it counts as a transport attempt: sleep 5 min, then double, until
`HARNESS2_LIMIT_MAX_WAIT` (default 2 h) is spent; the wait is logged with the reset time when the CLI
reports one. Only after that does the call raise `ProposerError`, and the stage falls back to its
input tree exactly as upstream does (recorded as `fell_back` with the error text, visible in
`harness2-report`'s fallback column). With `HARNESS2_STOP_ON_LIMIT=1` the limit raises a distinct
`UsageLimitError` that `run_sweep` turns into a clean exit, which is safe because re-running the
same command resumes from the cached summaries. The bridge answers a limit with HTTP 429 and a
`Retry-After` taken from the CLI when available; the upstream judges retry, and whatever stays
unscored is filled in by a later `harness2-judge`.

Audit, cache, harvest, retries and the concurrency semaphore are otherwise unchanged.

### 4.4 The judge bridge (`harness2/cli/judge_bridge.py`, script `harness2-judge-bridge`)

A stdlib `ThreadingHTTPServer` on `127.0.0.1` (port and key saved in
`dependencies/cli-bridge/bridge.json` and reused on restart, as the existing Codex bridge does;
bearer key required on every request). It prints the three `HARNESS2_JUDGE_*` lines to add to
`.env`, then serves:

- `GET /v1/models` – the two backends.
- `POST /v1/chat/completions` (JobBench). `messages` → one prompt: system message(s) become the
  system text, user content is a string or a list of parts; text parts concatenate; `image_url`
  parts (data URLs) are written to temp files and passed to Codex with `-i`, and on the Claude
  backend the request is refused with 422 so that rubric fails and the task is quarantined as
  judge-failed (unscored, never zero). `stream: true` → 400. `extra_body`
  `reasoning_effort` → the backend's effort flag. `temperature`, `max_completion_tokens` are
  accepted and ignored. Response: standard non-streaming chat completion with `choices[0].message.
  content`, `finish_reason: "stop"`, and `usage` filled from the CLI result where available.
- `POST /v1/responses` (LAB). `input` (string, or the list form reduced to its text) and
  `text.format` of type `json_schema` → the backend's structured-output flag. Response: the minimal
  Responses object the installed `openai` SDK needs for `response.output_text`
  (`output: [{type: "message", role: "assistant", content: [{type: "output_text", text, annotations: []}]}]`,
  plus `id`, `object: "response"`, `created_at`, `model`, `status: "completed"`, `usage`).

Backend selection from the model id, with an optional `cli/` prefix stripped:

- `claude:<model>` → `claude -p --output-format json --model <model> --effort <effort>
  --tools "" --setting-sources "" --strict-mcp-config --disable-slash-commands
  [--append-system-prompt <system>] [--json-schema <schema>]`, prompt on stdin, in an empty temp dir
  (same isolation flags and the same verification as the improver channel). With `--json-schema`, the structured object comes back in the result JSON
  (`structured_output`; the builder confirms the field name on 2.1.304) and is serialised as the text.
- `codex:<model>` → `codex exec --ephemeral --skip-git-repo-check --ignore-user-config -s read-only
  --json -m <model> -c model_reasoning_effort="<effort>" -o <tmp> [--output-schema <file>] -C
  <empty tmp dir> -`, prompt (system block prepended) on stdin.

Operational rules: a semaphore caps concurrent CLI processes (`--concurrency`, default 6, which
covers LAB's 6 evaluation threads; the JobBench adapter passes `--max-workers
HARNESS2_JUDGE_WORKERS`, default 6, so judge calls do not queue behind the bridge and burn their
240 s per-rubric client timeout waiting); a per-request wall (`--timeout`, default 600 s) returns a
504 JSON error the upstream judges retry; a CLI usage-limit failure returns 429 with `Retry-After`
(4.3); every call appends one line to
`dependencies/cli-bridge/usage.jsonl` (backend, model, tokens, cost, wall); `--log-dir` optionally
keeps prompt/response pairs. Auth or quota failures surface as 502 with the CLI's stderr tail.

Wiring: `HARNESS2_JUDGE_BASE_URL`/`_API_KEY` already feed JobBench (`--api-base`, `JUDGE_API_KEY`,
no gcloud call when the key is set); `JB_JUDGE_REASONING_EFFORT` becomes `HARNESS2_JUDGE_EFFORT`.
For LAB, the shipped patch `harvey-labs-judge-vertex-anthropic.diff` is regenerated against the
pinned commit to add: `cli/` prefix → provider `openai-compatible`, client
`openai.OpenAI(base_url=HARNESS2_JUDGE_BASE_URL, api_key=HARNESS2_JUDGE_API_KEY)`, model = the text
after the prefix, evaluation through the existing `_evaluate_openai` (Responses API). `preflight`
stops demanding gcloud for a `cli/` judge; `dependencies/ASSETS.json` is updated for the changed
patch so `scripts/check_provenance.py` stays green.

### 4.5 Benchmark adapters on pi

**JobBench.** `make_spec("pi")`: `install_mode="pi_workspace"`, `allow_subagents=False`, timeout
2400 s (as OpenCode). `JobBenchAdapter(substrate="pi")` builds `PiSubstrate(launcher=self._launch_pi)`.
The base harness is generated in code (four empty always-on files under
`runs/jb/_base_harness_pi_<pid>`, the way LAB already materialises its base), which is exactly the
upstream OpenCode v0 baseline: a bare agent with the stock system prompt. `_launch_pi` runs a new
first-party `harness2/benchmarks/jb_pi_runner.py`, a sibling of `jb_opencode_runner.py` with the
same environment contract (`TASK_IDS`, `LABEL`, `HARNESS_RENDER_DIR`, `TASKS_BASE_DIR`,
`TIMEOUT_PER_TASK`, `MAX_RETRIES`, plus `PI_BIN`, `PI_AGENT_DIR`, `MODEL_ID`, `PI_THINKING`,
`PI_TOOLS`), the same prompt text, the same workspace staging (`task_folder/` copy, empty `output/`,
`_rendered/` merged in), the same retry/reset loop, and the same outputs: event lines only in
`model_traj/<label>/<task>_<label>_attempt<n>_<stamp>.jsonl`, stderr in a `.log` beside it, the
Pi session under `model_traj/<label>/pi_sessions/`, deliverables moved to `model_output/<label>/`.
Exit status is non-zero when no deliverable landed, so the adapter leaves the rollout unmarked.
`trajectory()`, `deliverable_*`, `score()` and `preflight()` need no pi-specific logic beyond the
substrate preflight; `_run_judge` passes `--max-workers` from `HARNESS2_JUDGE_WORKERS` instead of
the hard-coded 8.

**LAB.** `lab_spec("pi")`: `install_mode="pi_workspace"`, `reserved_scripts=_runtime_scripts()`
(the base skills' helper scripts are in the workspace on this substrate too). Results under
`runs/lab/results/pi/`, stream name `pi_session.jsonl`. A new overlay
`third_party/lab_runner/pi/{run_pi.py, render_pi.py}` (installed to `<lab>/pi/`, listed in
`install.RUNNERS`) mirrors `opencode/run_opencode.py`: same task loading, same `build_prompt`, same
`results/<run_id>/{config.json, workspace/, output/, transcript.jsonl, metrics.json}` layout so
`evaluation.run_eval` scores it unchanged. Differences: the always-on text is the composed harness
plus `ENV_DELTA_PI` (the OpenCode delta reworded for Pi: real absolute paths, deliverables only
count under `output/`, binary documents only via `python3 scripts/read_doc.py`); LAB's base skills
load natively via `--skill <lab>/harness/skills/<name>` (skill mode `native`, the adapter's
default); the harness tree's skills and extensions load via their explicit flags; the runtime copies
`read_doc.py` last so no harness file can shadow it; `--doc-parser` defaults to `host` (the
sandbox mode needs podman); `metrics.json` is written last and only when the stream had parseable
events. The continue-nudge loop (`--max-continue 2`) resumes the saved session with `--session
<file>` and the same nudge text; if JSON-mode resume turns out not to work, the runner skips nudges
and says so in `config.json`. The base harness for pi is `systemprompt.md` = LAB's
`SYSTEM_PROMPT_PREAMBLE` + `ENV_DELTA_PI` (composed inside the LAB venv through `render_pi`), the
other three files empty. `_validate_pi(tree)` requires the always-on text to mention `output/` and
`read_doc.py`, as `_validate_cx` does.

**WorkBuddy.** `make_adapter("wb", …, substrate="pi")` raises
"WorkBuddy-Bench is not supported on the pi substrate (needs Docker and Harbor)".

### 4.6 Prompts (`core/prompts.py`)

A `_SURFACE_PI` block and `_TTS_SURFACE_HEAD_PI` (JobBench uses the `tts` pack) describe the Pi
surface in the same shape as the OpenCode and Codex blocks: the path table, the levers that are easy
to get wrong, the three discard rules, the deliverable-shape rule, and "how to make a change".
Pi-specific content, drawn from the Pi docs and `examples/extensions/hello.ts`:

- Skills: Agent Skills frontmatter (`name:` matching the directory, `description:` required or the
  skill is not loaded); the model is told the skill's path and reads SKILL.md itself; helper scripts
  travel at `skills/<name>/scripts/`; a skill must be named in the always-on text.
- Extensions: one TypeScript file exporting `default function (pi: ExtensionAPI)`;
  `pi.on("tool_call", async (event, ctx) => …)` may mutate `event.input` or return
  `{ block: true, reason }`; `pi.on("tool_result", …)` may return replacement `{ content }`;
  `pi.registerTool(defineTool({ name, description, parameters: Type.Object(…), execute }))` with
  `import { Type } from "@earendil-works/pi-ai"` and `import { defineTool, type ExtensionAPI } from
  "@earendil-works/pi-coding-agent"`; no UI, no timers, no processes in the factory; a handler that
  throws blocks the tool call, so keep handlers small and total.
- Subagents: unavailable; do not write one.
- `_build_surface_texts()` gets a `"pi"` entry (loop lever = an `extensions/<name>.ts` hook; trigger
  sentence = the always-on text).

### 4.7 Setup, check, CLI plumbing

- `_common.SUBSTRATES = ("oc", "cx", "pi")`; `cli/setup.py`, `cli/check.py` accept `pi`.
- `cli/setup.py`: clone/patch/`bun install` OpenCode only when `config.needs_opencode()`; always
  clone, patch, overlay and `uv sync` the benchmark; for `pi` also `install.pi_agent_dir()` (links,
  settings, catalog refresh). `--dry-run` prints the plan.
- `preflight.check`: the OpenCode preflight, pin and `node_modules` checks run only when OpenCode is
  needed; the opencode patch check likewise. The judge probes: for a `cli/` judge, TCP-connect to the
  bridge and require `HARNESS2_JUDGE_API_KEY`; for the improver channel, require the `claude` or
  `codex` binary (and `codex login status` succeeding for codex; Claude's auth is only learnable
  from a real call, which stays "first model call").
- `core/schedule.ns` error text and `_common` help mention `pi`.
- `pyproject.toml`: `harness2-judge-bridge = "harness2.cli.judge_bridge:main"`.
- `scripts/run_jobbench.sh` / `run_lab.sh` need no change (`SUBSTRATE=pi`).

### 4.8 Tests and verification

Unit tests (no model calls), added beside `tests/test_workflow.py`:

- `PI_SURFACE`: write/read/validate round trip; an extension and a skill accepted; a sub-agent
  rejected; prompts `_st("pi")` resolves and both packs render.
- `materialise()` output layout and the always-on composition (comment-only components render to
  nothing; `{{token}}` interpolation; scripts listing).
- `read_trajectory()` on a recorded Pi JSON stream fixture (from the smoke run, scrubbed): actions,
  observations, errors, usage totals.
- `resolve_channel_model` for `codex_cli`; `_claude_cli`/`_codex_cli` argv and env with
  `subprocess.run` mocked (nested-session variables stripped; Vertex only when asked).
- Judge bridge: spin it up on a free port with the CLI runner monkeypatched; round-trip
  `/v1/chat/completions` and `/v1/responses` with `urllib`; auth rejection; image placeholder;
  timeout → 504.
- `config.needs_opencode()` and `preflight.check` skipping OpenCode on `pi` (demo adapter + mocks).
- `harness2-demo` and the existing workflow tests stay green.

Live verification, in order, each a checkpoint Adam can stop at: (1) one `pi --mode json` smoke
run under the isolated agent dir, which also yields the fixture; (2) `harness2-setup jb --substrate
pi` + `harness2-check`; (3) `harness2-judge-bridge` up, `harness2 --bench jb --substrate pi --k 1
--mode parallel --only-domains <one> --max-tasks 1` (4 Pi rollouts, 3 improver sessions plus one
reflection), then `harness2-judge` and `harness2-report`; (4) LAB after `sudo apt-get install
pandoc`. Rough envelope per JobBench task at k=1: Pi rollouts are minutes each and cents on the
coding plan; each improver session is one agentic Claude/Codex run over a few hundred KB of
evidence; judging is one bridge call per rubric at low effort.

### 4.9 Known caveats to state in the docs

- Executor web access: Pi's `bash` can reach the internet, and JobBench rubrics are public on
  Hugging Face. Upstream's advice (egress block for `huggingface.co`, `hf.co`, `github.com`) stands;
  v1 does not enforce it. Results obtained without it should say so.
- Codex sessions report tokens, not dollars; the ledger's `usd` column is 0 for them.
- The bridge is a single local process; stopping it mid-judge leaves rows unscored, which a re-run of
  `harness2-judge` fills in.

## 5. Left to the builder

- The exact dedupe between `toolResult` `message_end` events and `tool_execution_end` in Pi's JSON
  stream, confirmed on a real stream.
- Whether Claude Code 2.1.304 returns `--json-schema` output under `structured_output` or in
  `result`; whether `--session <file>` resume works in `--mode json` (if not, LAB nudges are skipped).
- The smallest Responses-API object the `openai` version in the LAB venv accepts; confirmed by
  calling the bridge with that SDK in a test.
- Confirming with a `-e` smoke test that an extension outside the Pi package can import
  `@earendil-works/pi-ai` and `@earendil-works/pi-coding-agent` through Pi's jiti aliases; if not,
  the surface prompt restricts extensions to hook-only files with no imports.
- Exit-code handling for `timeout` (124/137) and how "no deliverable" is distinguished from a crash
  in the pi runner (mirror `jb_opencode_runner.py`).
- The Claude isolation experiment (4.3): which of `--setting-sources ""`, `CLAUDE_CODE_SIMPLE=1`
  or another combination both suppresses hooks/plugins/MCP and keeps OAuth auth, proven with the
  sentinel-hook test before any improver session runs.
- The exact text/shape of Claude's and Codex's usage-limit errors in headless mode, captured from a
  real occurrence or the CLIs' source, for the limit classifier.
- Module layout within the constraints above, docstrings, the `docs/PI.md` text, and the order of
  the implementation plan.

## 6. Decisions after the builder review (my calls; check these while reading)

1. `--bare` dropped (it disables OAuth on 2.1.304). Claude roles use `--setting-sources ""
   --strict-mcp-config --disable-slash-commands`, proven by a sentinel-hook test as the first build
   task; Codex becomes the default for both roles only if that proof fails.
2. Plan usage limits are waited out (up to 2 h, logged), then the stage falls back as upstream does;
   `HARNESS2_STOP_ON_LIMIT=1` stops the resumable sweep instead. The bridge returns 429.
3. Image-bearing judge requests pass through to Codex (`-i`) and are refused on Claude (422), so an
   image task is unscored rather than graded blind.
4. Concurrency is explicit: `HARNESS2_PI_CONCURRENCY=2` for rollouts, bridge `--concurrency 6`,
   `HARNESS2_JUDGE_WORKERS=6` for JobBench (upstream hard-coded 8).
