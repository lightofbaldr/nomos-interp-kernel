# Harness² on Pi

Run the Harness² loop with the [Pi coding agent](https://pi.dev) as the executor, Claude Code
or Codex as the improver, and the upstream benchmark judges served by those same CLIs. The
design is in `docs/superpowers/specs/2026-10-20-pi-substrate-cli-improver-judge-design.md`;
this page is the operator's guide plus what was verified on a real machine.

## Quick start (JobBench)

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[render]'
cp .env.example .env                       # solver, improver and judge settings
harness2-setup jb --substrate pi           # benchmark checkout + data; creates dependencies/pi/agent
harness2-judge-bridge                      # in another shell; copy its HARNESS2_JUDGE_* lines into .env
harness2-check --bench jb --substrate pi
harness2 --bench jb --substrate pi --k 1 --mode parallel --only-domains <occupation> --max-tasks 1
harness2-judge --bench jb --substrate pi --k 1 --mode parallel
harness2-report --bench jb --substrate pi --k 1 --mode parallel
```

Nothing here needs OpenCode, Docker or Google Cloud. LAB additionally needs `pandoc` and
`poppler-utils`; WorkBuddy-Bench is not available on this substrate (it needs Docker).

## How a rollout runs

1. `PiSubstrate.materialise` copies the harness tree and renders `_rendered/`: the four
   always-on files composed into `.harness2/APPEND_SYSTEM.md`, skills under `.pi/skills/` (each skill's helpers also mirrored to `skills/<name>/scripts/`,
   the path the improver is told to use),
   extensions under `.pi/extensions/`, helpers under `scripts/`.
2. `jb_pi_runner.py` stages a workspace (`task_folder/` inputs, empty `output/`, the rendered
   harness merged in) and runs one `pi --mode json` process with every discovery path off and
   the harness passed explicitly: `--append-system-prompt`, `--skill <dir>` per skill,
   `-e <file>` per extension.
3. The process runs under the isolated agent directory `dependencies/pi/agent`
   (`PI_CODING_AGENT_DIR`): its own `settings.json` (no packages, built-in extensions off,
   project trust never) and symlinks to your `~/.pi/agent/models.json` and `auth.json`, so
   your personal Pi setup never leaks in and no credential is copied.
4. The JSON event stream is the trajectory (`model_traj/<label>/*.jsonl`); deliverables are
   harvested from `output/` into `model_output/<label>/` (regular files only, symlinks
   skipped). A rollout that produced nothing is left unmarked and re-rolled next time.

### Safety of a rollout

- A runtime-owned guard extension (`harness2/substrate/pi_guard.ts`) is loaded before any
  harness extension and blocks `read`, `write`, `edit`, `ls`, `grep` and `find` calls whose
  path resolves outside the workspace or `/tmp`. This mirrors the `OPENCODE_PERMISSION`
  fence the OpenCode runner uses.
- The child environment is an allowlist (`PATH`, `HOME`, locale, `PI_*`, `XDG_*`, plus any
  names in `HARNESS2_PI_PASS_ENV`); `*_API_KEY` and `HARNESS2_*` secrets never reach a
  rollout. Pi takes provider keys from the linked `models.json`/`auth.json`.
- `bash` is not guarded: Pi has no sandbox, so a rollout can run any command the user can.
  For untrusted tasks, or before reporting results, run under an OS-level sandbox and apply
  the egress restrictions described in `docs/EXPERIMENTS.md` (JobBench rubrics are public).

## Settings

| Setting | Meaning |
|---|---|
| `HARNESS2_SOLVER_MODEL` | Pi `provider/model`, e.g. `zai/glm-5.3-flash`; must resolve under `dependencies/pi/agent` |
| `HARNESS2_PI_THINKING` | `pi --thinking` level (default `high`) |
| `HARNESS2_PI_TOOLS` | Pi `--tools` value; keep the additive `+name` form so extension tools stay active |
| `HARNESS2_PI_CONCURRENCY` | Cap on concurrent Pi rollouts across domain workers (default 2) |
| `HARNESS2_PI_BIN`, `HARNESS2_PI_PASS_ENV`, `HARNESS2_PI_MODELS_JSON`, `HARNESS2_PI_AUTH_JSON` | Binary, extra env names, and the credential files to link |

## The improver on Claude Code or Codex

`HARNESS2_IMPROVER_CHANNEL=claude_cli` runs each improver session as `claude -p` on your
login with `--setting-sources "" --strict-mcp-config --disable-slash-commands` (your hooks,
plugins, MCP servers and skills stay out; see the verified facts below) and full file access
to the session's `context/` tree. `codex_cli` runs `codex exec --ignore-user-config` with the
sandbox bypassed for the same reason. `HARNESS2_IMPROVER_MODEL` is a bare id or alias
(`opus`, `sonnet`, `gpt-5.6-sol`).

The default login isolation removes inherited `ANTHROPIC_API_KEY` along with nested
Claude session markers, so a parent session's stale key cannot override the login.
Explicit `HARNESS2_CLAUDE_ISOLATION=bare` keeps the API key instead. This applies to
both the improver and the Claude judge bridge; the parent environment is unchanged.

**Plan usage limits.** A usage or rate limit reported by the CLI is waited out inside the
call (5 minutes, doubling; 30 seconds for a rate limit) up to `HARNESS2_LIMIT_MAX_WAIT`
seconds, then the stage falls back to its input tree exactly as upstream does, visible as a
fallback in `harness2-report`. With `HARNESS2_STOP_ON_LIMIT=1` the sweep stops instead
(exit status 3); re-running the same command resumes from the cached summaries.

**Fallback-only adaptation.** If every improvement stage falls back, its mode is
not successful. A task with only such modes prints `fallback-only` (not `ok`), is
counted as failed, and makes the sweep exit 2. A mix of successful and fallback-only
modes is `partial`, also failed. Accepted no-op edits remain valid successes. The
fallback evidence and retained input trees are preserved, but no experience is
promoted from a fallback-only mode. Resuming an intact old all-fallback summary cannot turn
its historical `ok` into a successful skip or silently retry paid calls; after fixing
the cause, use an explicitly fresh run root for a new attempt.

## The judge bridge

`harness2-judge-bridge` serves `POST /v1/chat/completions` (JobBench) and `POST /v1/responses`
(LAB) on `127.0.0.1` behind a generated bearer key, running one `claude -p --tools ""` or
`codex exec -s read-only` process per call; the port and key persist under
`dependencies/cli-bridge/`. Model ids are `cli/claude:<model>` and `cli/codex:<model>`.
Images in a judge request are passed to Codex (`-i`) and refused on Claude (422), so an
image-bearing rubric is left unscored rather than graded blind. A usage limit becomes 429
with `Retry-After`; the upstream judges retry, and `harness2-judge` can be re-run later to
fill whatever stayed unscored. `--concurrency` (default 6) should stay at or above
`HARNESS2_JUDGE_WORKERS`. Every call appends a row to `dependencies/cli-bridge/usage.jsonl`.

**Which backend to judge with.** The default is `cli/claude:sonnet`: a tool-less `claude -p`
answers a 20k-token rubric prompt in seconds. The first live run used `cli/codex:gpt-5.6-sol`
with six judge workers: the first three calls took ten seconds each, then the ChatGPT plan
answered 429 to the parallel calls and every later call sat in Codex's reconnect backoff for
5 to 14 minutes, so three of eight rubrics finished inside the judge's wall and the task was
left unscored (never a zero). Use a Codex judge with `HARNESS2_JUDGE_WORKERS=1`, and keep
`HARNESS2_JUDGE_RUBRIC_TIMEOUT` (default 600 s) at or below the bridge `--timeout` so an
abandoned client call does not keep a bridge slot busy for longer than the client waits.
`HARNESS2_JUDGE_TIMEOUT` (default 3600 s) bounds one task's whole judge run.

Residual risk: Codex keeps its shell while judging (read-only, no network, `HOME` pointed at
a throwaway directory), so a prompt injection in a deliverable could still read a
world-readable file by absolute path and quote it into a local verdict file. The Claude
backend runs without tools.

## LAB on Pi

```bash
harness2-setup lab --substrate pi          # pinned checkout, LAB venv (document parsers), judge patch
harness2-check --bench lab --substrate pi
harness2 --bench lab --substrate pi --k 1 --mode parallel --only-domains employment-labor --max-tasks 1
harness2-judge --bench lab --substrate pi --k 1 --mode parallel
harness2-judge --bench lab --substrate pi --k 1 --mode parallel --bases
harness2-report --bench lab --substrate pi --k 1 --mode parallel
```

`harness2/benchmarks/lab_pi_runner.py` is the Pi twin of LAB's OpenCode runner. It is written
against the standard library and harness2 only, so it runs under harness2's own interpreter
and is unit-tested like the JobBench runner. For each rollout it:

1. **Stages** `results/<run_id>/workspace/`: the task's `documents/` (read-only copies), an
   empty `output/`, LAB's own `docx`/`pptx`/`xlsx` manuals under `.pi/skills/` (loaded with
   `--skill`, so Pi lists them in its system prompt) with their helper scripts under
   `skills/<name>/scripts/`, the rendered harness merged on top, and LAB's `read_doc.py`
   copied last into `scripts/` (the runtime wins over anything a harness ships).
2. **Runs** `pi --mode json` with the runtime guard loaded first and the task instructions
   verbatim after the workspace paths. `$WORKSPACE_DIR`, `$DOCUMENTS_DIR` and `$OUTPUT_DIR`
   are set, the LAB venv is first on `PATH` so `python3 scripts/read_doc.py <file>` parses
   `.docx`/`.pdf`/`.pptx`/`.xlsx` on the host (`LAB_DOC_PARSER=host`, `LAB_BENCH_ROOT`), and
   the environment is the same secret-free allowlist JobBench uses.
3. **Nudges**, as the OpenCode runner does: while `output/` is empty or the last message is
   not `DONE`, it resumes the saved session (`--session`) with the continue prompt, at most
   `--max-continue` (2) times, stopping when a resume adds no tool calls.
4. **Harvests** regular files from `workspace/output/` into `output/` (symlinks skipped),
   then writes `metrics.json` last, with Pi's own `exit_code`, `finished_cleanly`, the number
   of `continues` and `documents_read` (documents named in the agent's own tool calls). A run
   is recorded whenever the agent settled or delivered something, as upstream's runner does:
   a timeout after the memo was written keeps the memo and the trajectory, and a settled run
   that delivered nothing is the agent's result, judged as such. No parseable Pi events, or a
   stream cut off before `agent_settled` with nothing delivered, means no `metrics.json`, so
   an outage is re-rolled rather than cached as a zero.

The base harness of the `pi` cell is LAB's `harness/system_prompt.md` followed by a short
"Harness notes" delta: paths are real and absolute, deliverables go to `output/`, binary
documents are read through `scripts/read_doc.py`, skills are files. Validation requires those
two facts in the always-on files and refuses a harness skill named like a base skill (`docx`,
`pptx`, `xlsx`), because Pi keeps the first skill of a name and only warns about the second;
`pack.py`, `unpack.py` and the other base-skill scripts stay reserved names. Results live
under `runs/lab/results/pi/`; the trajectory is `pi_session.jsonl`, rendered by
`render_events`.

The judge is LAB's own (`evaluation.run_eval`) through the shipped patch, which also accepts
`cli/claude:<model>` and `cli/codex:<model>` ids: they go to the bridge's `/v1/responses`
endpoint with LAB's strict verdict schema. `HARNESS2_JUDGE_MODEL=cli/claude:sonnet` is passed
to LAB unchanged and `scores.json` records the model as `claude:sonnet`.

## Caveats

- Codex reports tokens, not dollars: the ledger's `usd` column is 0 for Codex sessions and
  bridge calls; Claude Code's cost estimate is recorded as it reports it.
- The bridge is one local process; stopping it mid-judge leaves rows unscored, which a later
  `harness2-judge` fills in.
- Web access is unrestricted, as upstream; see `docs/EXPERIMENTS.md`.

## Verified Pi behaviour (Pi 1.3.0, Node 22.23, 2026-10-20)

Smoke harness: a `count_files` skill, a `guard.ts` extension that blocks `rm -rf` through
`tool_call` and registers a `workspace_note` tool through `defineTool`, three dummy files,
solver `zai/glm-5.3-flash` at `--thinking low`. Two runs of about 10–18 s each.

- **Isolated agent directory works.** `PI_CODING_AGENT_DIR=dependencies/pi/agent` with
  `settings.json` (no packages, built-in extensions off, trust never) and symlinks to
  `~/.pi/agent/models.json` and `auth.json`. `pi update --models` under that directory wrote
  `models-store.json`, after which `pi --offline --list-models glm-5.3-flash` lists
  `zai glm-5.3-flash`. The user's 18 packages never load: stderr is empty.
- **Explicit resources load with discovery off.** `--no-extensions --no-skills
  --no-prompt-templates --no-themes --no-mcp --no-context-files --no-approve --offline` plus
  `--append-system-prompt <file>`, `--skill <dir>` and `-e <file>` give exactly the harness
  under test. The skill was advertised; the model read its `SKILL.md` with `read` from the
  `--skill` path and ran its helper with `python3 scripts/count.py` from the skill directory.
- **Extension imports resolve outside the Pi package.** `import { Type } from
  "@earendil-works/pi-ai"` and `import { defineTool, type ExtensionAPI } from
  "@earendil-works/pi-coding-agent"` load through Pi's jiti aliases from a file under the
  workspace. No build step.
- **`tool_call` blocking is visible to the model.** Returning `{ block: true, reason }`
  produces a `tool_execution_end` with `isError: true` and the reason as the result text; the
  model reported the block verbatim.
- **`--tools` must use the additive form.** A plain allowlist
  (`read,bash,edit,write,grep,find,ls`) silently excluded the extension-registered
  `workspace_note` tool ("No tool by that name is available"). `+grep,+find,+ls` keeps Pi's
  default read/bash/edit/write, adds the three, and leaves extension tools active, after
  which `workspace_note` was declared and called. `HARNESS2_PI_TOOLS` defaults to that form.
- **JSON stream shape.** `message_end` carries user, assistant (`thinking`, `text`,
  `toolCall` blocks, `usage` with `cost.total`) and `toolResult` messages; observations are
  taken from `tool_execution_end` (`toolName`, `result.content[].text`, `isError`), so
  `toolResult` messages are not rendered twice. About 180 `message_update` deltas per short
  run; `agent_settled` closes the run. Reported cost for the run: under one tenth of a cent.
- **Sessions and resume.** `--session-dir D` writes `D/<timestamp>_<uuid>.jsonl` directly
  (no per-project subfolder). Resuming in JSON mode with `--session <file>` and a new prompt
  works (the probe returned `DONE`), so LAB's continue-nudge is feasible on Pi.

## Headless CLI facts (Claude Code 2.1.304, Codex 0.163.0, 2026-10-20)

Probed from a scratch directory holding a project-level `.claude/settings.json` with a
`SessionStart` hook that touches a sentinel file, with the parent session's `CLAUDECODE` /
`CLAUDE_CODE_*` variables stripped and no `ANTHROPIC_API_KEY` set.

- **`--bare` is unusable with the login.** Its help text says auth is "strictly
  ANTHROPIC_API_KEY or apiKeyHelper … (OAuth and keychain are never read)". It is used only when
  `HARNESS2_CLAUDE_ISOLATION=bare` (API-key users).
- **`--setting-sources "" --strict-mcp-config --disable-slash-commands` isolates fully on the
  login.** `claude -p` authenticated with the OAuth login, the sentinel hook did not fire, and
  the model answered that its context held no "You have superpowers" text and no MCP tools,
  skills or slash commands. The control run without the three flags fired the sentinel and
  the model reported both present. These are the flags `clitools.claude_argv` emits.
- **`--tools` is variadic.** `--tools ""` placed last swallowed a positional prompt ("Input
  must be provided either through stdin or as a prompt argument"). The prompt always goes on
  stdin, and `claude_argv` emits `--tools` before the single-valued flags.
- **Structured output.** With `--json-schema`, the result JSON carries the object in
  `structured_output` and the same JSON serialised in `result`; `parse_claude_result`
  prefers `structured_output`.
- **Codex headless.** `printf … | codex exec --ephemeral --skip-git-repo-check
  --ignore-user-config -s read-only --json -m gpt-5.6-sol -o <file> -C <dir> -` wrote the
  reply to the `-o` file, emitted `thread.started`, `turn.started`, `item.completed`
  (`agent_message`) and `turn.completed` with `usage` (`input_tokens`, `cached_input_tokens`,
  `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`). With
  `--output-schema <file>` the structured JSON is the `agent_message` text and the `-o` file.
  The ChatGPT login was used; no API key.
- **No usage-limit or rate-limit error was met** during these probes, so the limit classifier
  still runs on the patterns in `clitools.py`; add the real text to
  `test_classify_failure_samples` when one appears.

## First run notes (JobBench `online_merchants/task1`, 2026-10-20)

Settings: solver `zai/glm-5.3-flash` at `--thinking high`, improver `claude_cli` / `opus` at
high effort, `--k 1 --mode parallel`, one task (3 input files, 20 KB).

- **Sweep: 46 minutes wall, status ok.** Four Pi rollouts (base1 8.5 min, 29 turns; inv1 18 min,
  48 turns; dom1 9 min, 23 turns; agg1 12.5 min, 28 turns), all four delivering both requested
  files (`Incident_Analysis_Report.xlsx`, `Supplier_Quality_Notice.pdf`). GLM cost for the four
  rollouts as reported by Pi: about $0.25. Ledger R=4, P=3 as the method specifies.
- **Improver sessions** (Claude Code, opus, high): `inv1` 139 s, `dom1`, `agg1` and the
  reflection; `harness2-report` puts the improver spend at $3.70 (Claude Code's own estimate;
  the subscription is what actually pays). Each session ran isolated (no hooks, plugins, MCP or
  skills) and edited `context/harness/` in place.
- **What the method did.** The invariant editor noticed that base1's PDF text came back garbled
  (unwrapped reportlab table cells) even though base1's own `'8.2%' in text` checks passed, and
  added `scripts/verify_outputs.py` plus a four-line tool-guidance note on wrapping table cells.
  The domain editor added a `complaint-root-cause-triage` skill with a helper script. The
  composer (`agg1`) merged both; the reflection kept the two edits with clean two-versus-two
  contrasts and wrote the first `online_merchants` library entry.
- **Trajectories** render through `render_events` with one observation per tool call; the
  runtime guard was loaded in every rollout and no file-tool call left the workspace.
- **Judging, first attempt (Codex, 6 workers): unscored, not zero.** Three rubric calls took
  ten seconds, then the ChatGPT plan rate-limited the parallel calls and the rest took 5 to 14
  minutes each; the judge subprocess hit its 30-minute wall with 3 of 8 rubrics done, and
  `judged.json` recorded the task as ungraded. This changed the default judge to
  `cli/claude:sonnet` and made the two judge timeouts settings (see "Which backend to judge
  with" above). The namespace's recorded judge was edited by hand for this one run, which is
  safe only because nothing had been graded under the first judge.
- **Judging, second attempt (Claude sonnet, 6 workers): graded.** 8 rubric calls, 165 s wall
  while stale Codex processes still held bridge slots, then `--bases` scored the base rollout in
  27 s (8 calls, about 15 s each when the bridge is idle). Judge cost as Claude Code reports it:
  about $0.85 per rollout.
- **Result on this one task (n=1, so a data point, not a finding):** base1 scored 38/58
  weighted (21 of 25 criteria), the adapted `agg1` scored 30/58 (19 of 25). The composed
  harness verified its PDF text and triaged complaints as instructed, and still lost points on
  two heavily weighted rubrics (benchmark comparison in the supplier notice; a dashboard sheet
  delivered as tables only). Judging the other candidates and running more tasks is what tells
  whether the method helps GLM-5.3-flash here; `harness2-judge --bases` gives the comparison
  row each time.

## First run notes (LAB `employment-labor/analyze-counterparty-markup-of-executive-employment-agreement`, 2026-10-20)

Settings: solver `zai/glm-5.3-flash` at `--thinking high`, improver `claude_cli` / `opus` at
high effort, judge `cli/claude:sonnet` at low effort, `--k 1 --mode parallel`, one task (five
documents, 201 KB: company draft, counterparty markup, playbook, client e-mail, comparables
spreadsheet; 59 rubric criteria).

- **Sweep: 81 minutes wall, status ok.** Four Pi rollouts (base1 16.8 min, 18 turns, 24 tool calls; inv1 16.4 min, 35 turns, 42 tool calls;
  dom1 21.6 min, 31 turns, 31 tool calls; agg1 30.2 min, 41 turns, 42 tool calls). Every rollout
  opened all five documents in its own tool calls (`scripts/read_doc.py` on the host parser for
  most of the Word and spreadsheet files, `read` for the e-mail, the domain skill's own helper and
  openpyxl in `dom1`) and delivered the memo as a `.docx` under `output/`. Every rollout took one
  continue-nudge, because the agent's last message is prose rather than `DONE`; each resume
  answered `DONE` without further tool calls. GLM cost for the four rollouts as Pi reports it:
  $0.47. (The run's `metrics.json` files carry the first version of `documents_read`, a
  substring count over the whole stream; the metric now counts documents named in the agent's
  own tool calls, which also gives 5/5 here.)
- **Improver sessions** (Claude Code, opus, high): `inv1` $2.03, `dom1` $3.25, the composer
  $0.75; `harness2-report` puts the whole improver spend including the reflection at $6.79
  (Claude Code's own estimate; the subscription is what actually pays).
- **What the method did.** The invariant editor found that `read_doc.py` renders a
  tracked-changes `.docx` as accepted text with no marks, that the base run hand-rolled an
  lxml extractor whose truncated output produced a false finding (a tracked change reported
  as untracked), and that LAB's own `docx` skill tells the agent to use `read` on a `.docx`
  (which returns raw bytes). It wrote a `redline-review` skill with a `docx_changes.py`
  helper (tracked and untracked changes with stable paragraph numbers) and a
  `binary-read-guard.ts` extension that blocks `read` on binary documents and appends a
  note after `read_doc.py` runs on a `.docx`. The domain editor wrote an
  `executive-agreement-redline-memo` practice skill with a `redline_compare.py` helper. The
  composer kept the domain skill and its trigger line and dropped the invariant edits. The
  reflection wrote the first `employment-labor` library entry and flagged the invariant
  skill for carrying task-specific "verified facts" that the agent took as given instead of
  finding; that warning is the entry's cue for the next proposer.
- **The extension worked in the tool loop.** Its `tool_result` note fired twice in `inv1`,
  and the agent ran the helper six times after reading the skill. `dom1` and `agg1` read
  their skill as the first action after `ls`.
- **Found and fixed by this run.** `inv1`'s first helper call failed with "No such file or
  directory": the proposer prompt promises a skill's helper at `skills/<name>/scripts/<file>`
  and tells the proposer to reference it that way, but the render placed harness skills only
  under `.pi/skills/`. `render_pi_workspace` now mirrors each skill's `scripts/` to
  `skills/<name>/scripts/` as well (JobBench had the same latent gap). The agent recovered
  with `find`, so the rollout still delivered.
- **Judging through the bridge: graded, fast.** `agg1` in 54 s and `base1` in 50 s wall
  (118 rubric calls, 118 ok, mean 4.5 s, max 8.5 s,
  Claude Code's cost estimate $13.96 in total). `scores.json` records
  `judge_model: "claude:sonnet"`; `judged.json` has 1 row, 1 graded.
- **Result on this one task (n=1, a data point, not a finding):** `base1` 54/59 criteria,
  the adapted `agg1` 56/59; neither all-pass. `agg1` recovered three of `base1`'s misses
  (the bonus-target change, the severance-versus-walk-away comparison, the five-issue
  executive summary) and lost one (the Board-nomination Good Reason trigger); both missed
  the 30-month severance framing and the market-practice framing of the 280G gross-up.
