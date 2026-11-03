# Pi Substrate + CLI Improver/Judge (JobBench) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Harness² end to end on JobBench with Pi (GLM-5.3-flash) as the executor, Claude Code or Codex as the improver, and the upstream judge talking to a local bridge backed by those CLIs.

**Architecture:** A new `pi` substrate renders the eight-component harness tree into a Pi workspace (appended system prompt, `--skill` dirs, `-e` extensions, scripts) and runs `pi --mode json` under an isolated agent directory; a shared `clitools` module drives `claude -p` and `codex exec` for both the improver channels in `core/proposer.py` and a stdlib HTTP bridge that serves OpenAI chat-completions and Responses endpoints to the unchanged upstream judges. Setup and preflight stop assuming OpenCode. LAB is a second plan, written after Task 12 passes.

**Tech Stack:** Python 3.12 stdlib only (no new runtime deps), `unittest`, `uv`, Pi 1.3.0 CLI, Claude Code 2.1.304, Codex 0.163.0.

**Spec:** `docs/superpowers/specs/2026-10-20-pi-substrate-cli-improver-judge-design.md`

## Global Constraints

- Python `>=3.12,<3.14`; `pyproject.toml` `dependencies = []` stays empty; ruff line length 100, `select = ["E4","E7","E9","F","I"]`.
- Tests are `unittest` modules under `tests/`, run as `.venv/bin/python -m unittest tests.<module> -v`; `tests/test_workflow.py` and `harness2-demo --output runs/demo --k 2` must stay green after every task.
- Names fixed by the spec: substrate `pi`, install mode `pi_workspace`, channels `claude_cli` / `codex_cli` / `opencode`, judge model ids `cli/claude:<model>` and `cli/codex:<model>`, env vars exactly as the spec's `.env` block (`HARNESS2_SOLVER_MODEL`, `HARNESS2_PI_THINKING`, `HARNESS2_PI_TOOLS`, `HARNESS2_PI_BIN`, `HARNESS2_PI_CONCURRENCY`, `HARNESS2_IMPROVER_CHANNEL`, `HARNESS2_IMPROVER_MODEL`, `HARNESS2_IMPROVER_EFFORT`, `HARNESS2_CLAUDE_USE_VERTEX`, `HARNESS2_LIMIT_MAX_WAIT`, `HARNESS2_STOP_ON_LIMIT`, `HARNESS2_JUDGE_MODEL`, `HARNESS2_JUDGE_BASE_URL`, `HARNESS2_JUDGE_API_KEY`, `HARNESS2_JUDGE_EFFORT`, `HARNESS2_JUDGE_WORKERS`).
- Never write a provider key anywhere: the Pi agent dir links `models.json`/`auth.json`, the bridge key is generated into `dependencies/cli-bridge/bridge.json` (mode 0600).
- "A judge or runner failure is `None`, never a zero, and never cached as complete" holds in every new path.
- `--bare` is never passed to `claude` unless `HARNESS2_CLAUDE_ISOLATION=bare` (API-key users only).
- Upstream OpenCode/Codex/Vertex paths stay functional; OpenCode is required only when `config.needs_opencode()` is true.
- Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Commit locally; never push.

## Review Focus

1. A Pi rollout that exits 0 but leaves `output/` empty (the model answered in chat) must count as "no deliverable": runner exit 1, rollout unmarked, nothing moved to `model_output/`. Pinned by `test_run_one_without_output_is_failure` (Task 10).
2. A Pi event stream cut mid-line by the wall timeout must render what exists and end with `[ERROR] stream ended before agent_settled`, never raise. Pinned by `test_render_events_torn_stream` (Task 6).
3. A judge prompt of hundreds of KB (JobBench flattens every deliverable) must reach the CLI on stdin, never argv. Pinned by `test_run_backend_sends_prompt_on_stdin` (Task 9).
4. Plan-limit wording varies ("usage limit reached", "rate_limit_error", "429 Too Many Requests", "resets at 3pm"); the classifier must catch each and the wait loop must not spin past `HARNESS2_LIMIT_MAX_WAIT`. Pinned by `test_classify_failure_samples` (Task 3) and `test_usage_limit_waits_then_succeeds` (Task 4).
5. The solver model not resolvable under the isolated agent dir (missing link or catalog) must fail preflight with the remedy, before any rollout. Pinned by `test_preflight_missing_model_names_remedy` (Task 6).

---

### Task 1: Baseline environment and repo hygiene

**Files:**
- Create: `.gitignore`
- Commit: `docs/superpowers/specs/2026-10-20-pi-substrate-cli-improver-judge-design.md`, `docs/superpowers/plans/2026-10-20-pi-substrate-jobbench.md`

**Interfaces:**
- Produces: a `.venv` at the repo root that every later `Run:` line uses.

- [ ] **Step 1: Create the environment**

Run: `cd ~/Projects/harness2 && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[render]'`
Expected: ends with `Installed … harness2`.

- [ ] **Step 2: Baseline tests and demo**

Run: `.venv/bin/python -m unittest tests.test_workflow -v && .venv/bin/harness2-demo --output runs/demo --k 2`
Expected: `OK` for 5 tests; demo prints `Local demo: 2 tasks completed; 0 failed.`

- [ ] **Step 3: Write `.gitignore`**

Contents, one per line: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `runs/`, `splits/`, `dependencies/*`, `!dependencies/ASSETS.json`, `!dependencies/PINNED.md`, `!dependencies/README.md`.

- [ ] **Step 4: Verify the ignore list**

Run: `git status --short`
Expected: only `.gitignore` and the two `docs/superpowers/` files are listed.

- [ ] **Step 5: Commit**

```bash
git add .gitignore docs/superpowers
git commit -m "docs: Pi substrate design and plan; ignore local state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Settings and channel spec

**Files:**
- Modify: `harness2/config.py:99-102` (model ids), `:154` (judge effort), `:174-178` (channels), `:181-190` (`validate_models`)
- Modify: `harness2/core/spec.py:33` (`CHANNELS`), `:51-98` (`resolve_channel_model`), `:129` (`INSTALL_MODES`)
- Test: `tests/test_config_channels.py`

**Interfaces:**
- Produces (config): `SOLVER_MODEL_PI: str`, `PI_BIN: str`, `PI_THINKING: str`, `PI_TOOLS: str`, `PI_CONCURRENCY: int`, `PI_AGENT_DIR: Path` (= `DEPENDENCIES/"pi"/"agent"`), `IMPROVER_CHANNEL: str`, `IMPROVER_EFFORT: str`, `CLAUDE_USE_VERTEX: bool`, `CLAUDE_ISOLATION: str` (`"flags"` default, `"bare"`), `LIMIT_MAX_WAIT: int`, `STOP_ON_LIMIT: bool`, `JUDGE_EFFORT: str`, `JUDGE_WORKERS: int`, `JUDGE_BRIDGE_DIR: Path` (= `DEPENDENCIES/"cli-bridge"`), `DEFAULT_PROPOSER_CHANNEL = IMPROVER_CHANNEL`, `PROPOSER_MODEL_BY_CHANNEL` with a `"codex_cli"` entry, `JB_JUDGE_REASONING_EFFORT = JUDGE_EFFORT`, `def needs_opencode(substrate: str, channel: str | None = None) -> bool`, `def judge_is_cli() -> bool`.
- Produces (spec): `CHANNELS == ("claude_cli", "codex_cli", "opencode")`; `INSTALL_MODES` gains `"pi_workspace"`; `resolve_channel_model` treats `codex_cli` exactly like `claude_cli` (bare id, `/` rejected).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_channels.py
class ChannelSpecTests(unittest.TestCase):
    def test_codex_channel_takes_bare_id(self):
        ch = resolve_channel_model("codex_cli", "gpt-5.6-sol", effort="high")
        self.assertEqual((ch.kind, ch.model, ch.effort), ("codex_cli", "gpt-5.6-sol", "high"))
    def test_cli_channels_reject_provider_prefix(self):
        for kind in ("claude_cli", "codex_cli"):
            with self.assertRaises(ValueError):
                resolve_channel_model(kind, "anthropic/opus")
    def test_pi_workspace_is_an_install_mode(self):
        self.assertIn("pi_workspace", INSTALL_MODES)

class ConfigTests(unittest.TestCase):
    def test_needs_opencode(self):
        self.assertTrue(config.needs_opencode("oc", "claude_cli"))
        self.assertTrue(config.needs_opencode("pi", "opencode"))
        self.assertFalse(config.needs_opencode("pi", "claude_cli"))
        self.assertFalse(config.needs_opencode("cx", "codex_cli"))
    def test_validate_models_cli_improver(self):
        # patch BOTH os.environ (require() reads it) and the module constants
        with patch.dict(os.environ, {"HARNESS2_SOLVER_MODEL": "zai/glm-5.3-flash"}), \
             patch.object(config, "SOLVER_MODEL_OC", "zai/glm-5.3-flash"), \
             patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"):
            with patch.object(config, "IMPROVER_MODEL", "opus"):
                config.validate_models()
            with patch.object(config, "IMPROVER_MODEL", "anthropic/opus"), self.assertRaises(config.ConfigError):
                config.validate_models()
    def test_validate_models_cli_judge_needs_bridge(self):
        with patch.dict(os.environ, {"HARNESS2_SOLVER_MODEL": "zai/x", "HARNESS2_JUDGE_MODEL": "cli/codex:gpt-5.6-sol"}), \
             patch.object(config, "SOLVER_MODEL_OC", "zai/x"), patch.object(config, "IMPROVER_MODEL", "zai/x"), \
             patch.object(config, "JUDGE_MODEL", "cli/codex:gpt-5.6-sol"), patch.object(config, "JB_JUDGE_API_BASE", ""):
            with self.assertRaisesRegex(config.ConfigError, "HARNESS2_JUDGE_BASE_URL"):
                config.validate_models(judge=True)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m unittest tests.test_config_channels -v`
Expected: FAIL/ERROR (`AttributeError: needs_opencode`, `ValueError: unknown channel 'codex_cli'`).

- [ ] **Step 3: Implement**

In `config.py`: the constants listed in Interfaces, read with `env`/`env_int` and the spec defaults (`PI_TOOLS` default `read,bash,edit,write,grep,find,ls`, `PI_CONCURRENCY` 2, `LIMIT_MAX_WAIT` 7200, `JUDGE_WORKERS` 6, `JUDGE_EFFORT` `low`, `IMPROVER_CHANNEL` `opencode`, `IMPROVER_EFFORT` `high`); `SOLVER_MODEL_PI = SOLVER_MODEL_OC`. `needs_opencode` returns `substrate == "oc" or (channel or DEFAULT_PROPOSER_CHANNEL) == "opencode"`. `validate_models`: solver must contain `/`; improver must contain `/` on `opencode` and must not on the CLI channels (message: "claude_cli/codex_cli take a bare model id such as opus or gpt-5.6-sol"); with `judge=True` and `judge_is_cli()`, require `JB_JUDGE_API_BASE` and `JB_JUDGE_API_KEY` (message names both env vars and "harness2-judge-bridge"). In `spec.py`: extend `CHANNELS`, `INSTALL_MODES`; generalise the two `kind == "claude_cli"` checks to `kind in ("claude_cli", "codex_cli")`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m unittest tests.test_config_channels tests.test_workflow -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness2/config.py harness2/core/spec.py tests/test_config_channels.py
git commit -m "feat(config): pi substrate settings and codex_cli channel"
```
(+ trailer)

---

### Task 3: Shared CLI tooling (`clitools`)

**Files:**
- Create: `harness2/core/clitools.py`
- Modify: `harness2/core/proposer.py:407-430` (move `_parse_json` body to `clitools.parse_json_object`, keep a thin alias)
- Test: `tests/test_clitools.py`

**Interfaces:**
- Produces:
  - `NESTED_ENV_KEYS = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT")`, `NESTED_ENV_PREFIXES = ("CLAUDE_CODE_",)`; `def strip_nested_env(env: Mapping[str, str]) -> dict[str, str]`.
  - `CLAUDE_ISOLATION_FLAGS = ("--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands")`.
  - `def claude_argv(*, model: str, effort: str, system: str = "", tools: str | None = None, json_schema: str | None = None, skip_permissions: bool = False, isolation: str = "flags", binary: str | None = None) -> list[str]` → `[claude, "-p", "--output-format", "json", ("--dangerously-skip-permissions")?, "--model", model, "--effort", effort, *isolation-flags-or-"--bare", ("--tools", tools)?, ("--append-system-prompt", system)?, ("--json-schema", json_schema)?]`.
  - `def codex_argv(*, model: str, effort: str, cwd: Path, last_message: Path, sandbox: str = "full", schema_file: Path | None = None, images: Sequence[Path] = (), binary: str | None = None) -> list[str]` → `[codex, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--json", "-C", cwd, "-m", model, "-c", 'model_reasoning_effort="<effort>"', "-o", last_message, <"--dangerously-bypass-approvals-and-sandbox" | "-s", "read-only">, ("--output-schema", f)?, ("-i", img)*, "-"]`.
  - `@dataclass class CliReply: text: str = ""; cost_usd: float = 0.0; usage: dict = field(default_factory=dict); error: str = ""; limited: bool = False` with `ok` property (`bool(text.strip()) and not error`).
  - `def parse_json_object(stdout: str) -> dict` (the former `_parse_json`).
  - `def parse_claude_result(stdout: str, returncode: int, stderr: str) -> CliReply` (text from `result`/`structured_output` (json-dumped when a dict), cost from `total_cost_usd`, usage from `usage`, error when `is_error` or `subtype` not in (None, "success") or rc≠0 with empty text; `limited = classify_failure(error or text) is not None`).
  - `def parse_codex_result(last_message: Path, stdout: str, returncode: int, stderr: str) -> CliReply` (text from the `-o` file, else last `agent_message` item; usage summed from `turn.completed`; error from `turn.failed`/`error` items or rc≠0).
  - `def classify_failure(text: str) -> str | None` → `"usage_limit"` | `"rate_limit"` | `None`; `def retry_after_seconds(text: str) -> int | None`; `def codex_system_block(system: str, mission: str) -> str` → `"# SYSTEM INSTRUCTIONS\n\n{system}\n\n# END\n\n{mission}"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_strip_nested_env(self):
    out = strip_nested_env({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "7", "PATH": "/x"})
    self.assertEqual(out, {"PATH": "/x"})
def test_claude_argv_flags_isolation(self):
    argv = claude_argv(model="opus", effort="high", system="S", skip_permissions=True)
    self.assertEqual(argv[1:4], ["-p", "--output-format", "json"])
    for flag in ("--dangerously-skip-permissions", "--setting-sources", "--strict-mcp-config", "--disable-slash-commands"):
        self.assertIn(flag, argv)
    self.assertNotIn("--bare", argv); self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "S")
def test_claude_argv_bare_only_when_asked(self):
    self.assertIn("--bare", claude_argv(model="opus", effort="low", isolation="bare"))
def test_codex_argv(self):
    argv = codex_argv(model="gpt-5.6-sol", effort="high", cwd=Path("/w"), last_message=Path("/w/last.txt"))
    self.assertEqual(argv[-1], "-"); self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
    self.assertIn('model_reasoning_effort="high"', argv); self.assertIn("--ignore-user-config", argv)
    ro = codex_argv(model="m", effort="low", cwd=Path("/w"), last_message=Path("/l"), sandbox="read-only", images=[Path("/i.png")])
    self.assertEqual(ro[ro.index("-s") + 1], "read-only"); self.assertEqual(ro[ro.index("-i") + 1], "/i.png")
def test_parse_claude_result(self):
    ok = parse_claude_result('{"result":"hi","total_cost_usd":0.5,"subtype":"success","usage":{"input_tokens":3}}', 0, "")
    self.assertEqual((ok.text, ok.cost_usd, ok.usage["input_tokens"], ok.ok), ("hi", 0.5, 3, True))
    bad = parse_claude_result('{"is_error":true,"result":"You have reached your usage limit. Resets at 4pm","subtype":"error"}', 1, "")
    self.assertEqual((bad.ok, bad.limited), (False, True))
    structured = parse_claude_result('{"result":"x","structured_output":{"verdict":"pass"},"subtype":"success"}', 0, "")
    self.assertEqual(json.loads(structured.text), {"verdict": "pass"})
def test_parse_codex_result_prefers_last_message_file(self):  # tmp file "done"; stdout has turn.completed usage {"input_tokens":10,"output_tokens":2}
    ...assertEqual((r.text, r.usage["input_tokens"], r.ok), ("done", 10, True))
def test_parse_codex_result_falls_back_to_stream(self):  # no file; stdout item agent_message "from stream"
def test_classify_failure_samples(self):
    for s in ("usage limit reached", "You've hit your limit", "weekly limit", "quota exceeded"):
        self.assertEqual(classify_failure(s), "usage_limit", s)
    for s in ("rate_limit_error", "429 Too Many Requests", "overloaded_error", "Rate limit"):
        self.assertEqual(classify_failure(s), "rate_limit", s)
    self.assertIsNone(classify_failure("SyntaxError in extension"))
    self.assertEqual(retry_after_seconds("retry after 90 seconds"), 90); self.assertIsNone(retry_after_seconds("no hint"))
def test_codex_system_block(self): startswith "# SYSTEM INSTRUCTIONS\n\n" and endswith "# END\n\nM"
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: harness2.core.clitools`.

- [ ] **Step 3: Implement `harness2/core/clitools.py`** per Interfaces; `classify_failure` is a case-insensitive regex table (usage: `usage[ _-]?limit|hit your limit|reached your limit|weekly limit|quota`; rate: `rate[ _-]?limit|too many requests|\b429\b|overloaded`); `retry_after_seconds` matches `retry.after (\d+)` and `in (\d+) seconds`. Move `_parse_json` from `proposer.py` into `parse_json_object`; leave `proposer._parse_json = clitools.parse_json_object`.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m unittest tests.test_clitools tests.test_workflow -v` → PASS.

- [ ] **Step 5: Commit** — `git add harness2/core/clitools.py harness2/core/proposer.py tests/test_clitools.py && git commit -m "feat: shared headless Claude/Codex CLI tooling"` (+ trailer)

---

### Task 4: Improver channels in `Proposer`

**Files:**
- Modify: `harness2/core/proposer.py:46-58` (`_DROP_ENV` use), `:91-125` (`Proposer.env`), `:126-208` (`call` loop), `:210-252` (`_claude_cli`), add `_codex_cli` after it, `:253-311` (`_opencode` returns `CliReply`), `_write_audit` meta
- Test: `tests/test_proposer_channels.py`

**Interfaces:**
- Consumes: Task 3 `clitools.*`, Task 2 `config.CLAUDE_USE_VERTEX`, `CLAUDE_ISOLATION`, `LIMIT_MAX_WAIT`, `STOP_ON_LIMIT`.
- Produces: `class UsageLimitError(RuntimeError)` (not a `ProposerError`, so `_run_stage` does not swallow it) with `.reset_hint: str`; `Proposer.stopped: bool` (set before raising); the three channel methods all return `CliReply`; `Proposer._wait_limit(reply: CliReply, waited: int) -> int` (sleeps `min(300 * 2**n, remaining)` and returns the new total; raises `UsageLimitError` when `config.STOP_ON_LIMIT`).

- [ ] **Step 1: Write the failing tests** (every test patches `harness2.core.proposer.subprocess.run` with a recording fake and `time.sleep`)

```python
def test_claude_argv_and_env(self):  # ChannelSpec("claude_cli", "opus", effort="high"); fake returns stdout '{"result":"<notes>ok</notes>","total_cost_usd":0.5,"subtype":"success"}'
    with patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x"}):
        reply = Proposer(spec).call("mission", None, "lbl")
    argv, kw = fake.calls[0]
    self.assertEqual(reply.text, "<notes>ok</notes>"); self.assertEqual(reply.cost_usd, 0.5)
    self.assertIn("--model", argv); self.assertEqual(argv[argv.index("--model") + 1], "opus")
    self.assertIn("--setting-sources", argv); self.assertNotIn("--bare", argv)
    self.assertNotIn("CLAUDECODE", kw["env"]); self.assertNotIn("CLAUDE_CODE_USE_VERTEX", kw["env"]); self.assertEqual(kw["input"], "mission")
def test_claude_vertex_opt_in(self):  # patch config.CLAUDE_USE_VERTEX True, ANTHROPIC_API_KEY in os.environ
    self.assertEqual(kw["env"]["CLAUDE_CODE_USE_VERTEX"], "1"); self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])
def test_codex_channel_reads_last_message(self):  # ChannelSpec("codex_cli", "gpt-5.6-sol"); fake writes "<notes>done</notes>" to argv[argv.index("-o")+1], rc 0
    self.assertEqual(reply.text, "<notes>done</notes>"); self.assertEqual(argv[1], "exec"); self.assertEqual(argv[-1], "-")
    self.assertIn("--ignore-user-config", argv); self.assertTrue(kw["input"].startswith("# SYSTEM INSTRUCTIONS"))  # when system="S" passed
def test_usage_limit_waits_then_succeeds(self):  # fake: limited error twice, then success; config.LIMIT_MAX_WAIT patched 1000
    self.assertEqual(sleeps, [300, 600]); self.assertTrue(reply.ok); self.assertEqual(reply.attempts, 1)
def test_usage_limit_budget_exhausted_falls_back_to_proposer_error(self):  # LIMIT_MAX_WAIT 0 → no sleeps; 2 attempts; ProposerError message contains "usage limit"
def test_stop_on_limit_raises_usage_limit_error(self):  # config.STOP_ON_LIMIT True → UsageLimitError, prop.stopped is True, no sleep
def test_opencode_channel_still_returns_text(self):  # keeps tests.test_workflow's fake stdout '{"type":"text","part":{"text":"done"}}' working
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m unittest tests.test_proposer_channels -v` → FAIL (`--bare` present / `codex_cli` unknown / no `UsageLimitError`).

- [ ] **Step 3: Implement**: `env()` = `strip_nested_env(os.environ)`, then the Vertex block only when `config.CLAUDE_USE_VERTEX` (claude) or the Google block (opencode) as today. `call()`: the per-attempt body calls the channel method chosen by `{"claude_cli": self._claude_cli, "codex_cli": self._codex_cli, "opencode": self._opencode}[kind]`; when the reply is not ok and `reply.limited`, call `_wait_limit` and `continue` without consuming an attempt while the wait budget lasts; otherwise unchanged. `_claude_cli` builds argv with `clitools.claude_argv(model=…, effort=…, system=…, skip_permissions=True, isolation=config.CLAUDE_ISOLATION)` keeping the `SYSTEM_ARG_MAX` fallback. `_codex_cli` writes nothing to disk except the `-o` path `cwd/".harness2_last_message.txt"`. `_write_audit` adds `"usage": reply.usage`.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m unittest tests.test_proposer_channels tests.test_workflow -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(proposer): claude_cli without Vertex, codex_cli channel, usage-limit wait"` (+ trailer)

---

### Task 5: Pi surface and prompts

**Files:**
- Create: `harness2/substrate/pi.py` (surface only in this task)
- Modify: `harness2/substrate/__init__.py:22-33` (export `PiSubstrate`, `PI_SURFACE` — the class lands in Task 6; export the surface now)
- Modify: `harness2/core/prompts.py` after `:294` (`_SURFACE_PI`), after `:515` (`_TTS_SURFACE_HEAD_PI`), `:617-662` (`_build_surface_texts`)
- Test: `tests/test_pi_surface.py`

**Interfaces:**
- Produces: `PI_SURFACE: SurfaceSpec` with `name="pi"`, `always_on=("systemprompt.md", "guardrails.md", "LongTermMEMORY.md", "tool_descriptions/tool_guidance.md")`, `always_on_max=24_000`, `skill_dir="skills"`, `skill_name_rule="must_match"`, `autotrigger_file="guardrails.md"`, `trigger_line="- When {desc}, read the `{name}` skill's SKILL.md and follow it.\n"`, `subagent_requires_mode=False`, `plugin_dir="extensions"`, `hooks_file=None`, `compile_skill_scripts=True`, `reject_empty_always_on=False`, stubs for `scripts/README.md`, `sub_agents/README.md` ("not available on this substrate"), `extensions/README.md`, `skills/README.md`.
- Produces: `prompts._st("pi")` resolves; `prompts.propose/aggregate/compose_round/experience` accept `PI_SURFACE` or `"pi"`.

- [ ] **Step 1: Write the failing tests**

```python
TREE = {a: "" for a in PI_SURFACE.always_on} | {
    "guardrails.md": "- When counting files, read the `count_files` skill's SKILL.md and follow it.\n",
    "skills/count_files/SKILL.md": "---\nname: count_files\ndescription: Count files\n---\nRun `python3 skills/count_files/scripts/count.py`.\n",
    "skills/count_files/scripts/count.py": "print(1)\n", "scripts/check.py": "print('ok')\n",
    "extensions/guard.ts": "export default function (pi) {}\n"}
def test_roundtrip(self): write_tree(PI_SURFACE, TREE, d); self.assertEqual(read_tree(PI_SURFACE, d), TREE)  # stubs excluded
def test_validate_accepts_tree(self): self.assertEqual(validate(PI_SURFACE, TREE, allow_subagents=False)["plugins"], 1)
def test_validate_rejects_subagent(self): assertRaisesRegex(ValueError, "sub_agents are not available")
def test_validate_rejects_untriggered_skill(self): tree without the guardrails line → ValueError mentioning "count_files"
def test_autotrigger_line_format(self): autotrigger(PI_SURFACE, tree) == ["count_files"]; guardrails ends with "read the `count_files` skill's SKILL.md and follow it.\n"
def test_prompts_render_for_pi(self):
    adapt = BenchPrompts(work="w"); tts = BenchPrompts(work="w", prompt_pack="tts")
    for text in (propose(adapt, "INV", PI_SURFACE), propose(tts, "DOM", "pi"), aggregate(tts, "pi"), compose_round(adapt, "pi", t=1, k=1)):
        self.assertIn("extensions/<name>.ts", text); self.assertIn("tool_call", text)
        for bad in ("plugin/<name>.ts", ".codex/hooks.json", "OpenCode", "Jinja"): self.assertNotIn(bad, text)
```

- [ ] **Step 2: Run to verify failure** — `ImportError` / `ValueError: no prompt surface for 'pi'`.

- [ ] **Step 3: Implement `PI_SURFACE`** in `harness2/substrate/pi.py` (module docstring: what the substrate is; `SurfaceSpec` values above; stub texts in the style of `CODEX_SURFACE.stubs`).

- [ ] **Step 4: Implement the prompt blocks.** `_SURFACE_PI` mirrors `_SURFACE_CODEX` in structure and length and must contain the literal anchors `THREE RULES THAT DISCARD`, `THE DELIVERABLE'S SHAPE` and `# HOW TO MAKE A CHANGE` (the `_surface_part` slices use them; copy the deliverable-shape and how-to-change paragraphs verbatim from `_SURFACE_OPENCODE`). Content it must carry: the path table (always-on four files appended to Pi's stock system prompt; `skills/<name>/SKILL.md` advertised by name+description, body read on demand with the `read` tool; `skills/<name>/scripts/*`; `scripts/<name>.py` via `python3 scripts/<name>.py`; `extensions/<name>.ts` always loaded, acts inside the tool loop; `sub_agents/` not available, never write one); the skill rules (frontmatter `name:` equal to the directory and `description:` required or the skill is not loaded; must be named in the always-on text; helpers travel with it); the extension contract (`export default function (pi: ExtensionAPI)`; `pi.on("tool_call", async (event, ctx) => …)` with `event.toolName`, mutate `event.input` in place, return `{ block: true, reason: "…" }`; `pi.on("tool_result", …)` returning `{ content: [{ type: "text", text }] }`; `pi.registerTool(defineTool({ name, description, parameters: Type.Object({…}), async execute(_id, params) { return { content: [{ type: "text", text }], details: undefined } } }))` with the two imports `import { Type } from "@earendil-works/pi-ai"` and `import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent"`; no UI, timers, processes or network in the factory; a throwing `tool_call` handler blocks the call); the three discard rules (1 trigger line, 2 every extension must load — a bad import or syntax error is reported at startup and a throwing handler blocks every matching call, so keep extensions small and total, 3 every named script must exist). `_TTS_SURFACE_HEAD_PI` mirrors `_TTS_SURFACE_HEAD_CODEX` with the same table and hard rules. In `_build_surface_texts` add `"pi"` with `inv` lever text "`extensions/<name>.ts` hook -- for example, a `tool_call` handler that blocks a finish before a {deliverable} exists, with a corrective reason" and `dom` trigger sentence "A skill exists only once its name appears in the always-on text: write that trigger line in the same edit."; `tts_surface` = head + `_surface_part(_SURFACE_PI, "THREE RULES THAT DISCARD", "THE DELIVERABLE'S SHAPE")` + `_surface_part(_SURFACE_PI, "# HOW TO MAKE A CHANGE")`.

- [ ] **Step 5: Run tests** — `.venv/bin/python -m unittest tests.test_pi_surface tests.test_workflow -v` → PASS; then `uvx ruff check harness2 tests` → no errors.

- [ ] **Step 6: Commit** — `git commit -m "feat(pi): editable surface and improver prompts for the Pi substrate"` (+ trailer)

---

### Task 6: Pi substrate runtime, trajectory renderer, isolated agent dir

**Files:**
- Modify: `harness2/substrate/pi.py` (add everything below), `harness2/substrate/__init__.py` (export `PiSubstrate`)
- Modify: `harness2/install.py` after `:67` (add `pi_agent_dir`)
- Test: `tests/test_pi_substrate.py`

**Interfaces:**
- Consumes: Task 2 config constants; Task 5 `PI_SURFACE`.
- Produces (`harness2/substrate/pi.py`):
  - `RENDERED = "_rendered"`, `APPEND_SYSTEM_REL = ".harness2/APPEND_SYSTEM.md"`.
  - `def compose_append_system(staged: Path, ctx: Mapping[str, str]) -> str` — the body of today's `jobbench.compose_agent_body` moved here unchanged (`{{token}}` interpolation, HTML comments stripped, headings `# Operating Guardrails`, `# Long-Term Memory`, `# Helper scripts (run via Bash)`).
  - `@dataclass(frozen=True) class RenderedHarness: root: Path; append_system: Path | None; skills: tuple[Path, ...]; extensions: tuple[Path, ...]; scripts: tuple[str, ...]`; `def render_pi_workspace(staged: Path, render_dir: Path, ctx: Mapping[str, str]) -> RenderedHarness` (writes `render_dir/.harness2/APPEND_SYSTEM.md` only when the body is non-blank, copies `skills/<n>/` whole to `.pi/skills/<n>/`, `extensions/*.ts` to `.pi/extensions/`, `scripts/*.py` to `scripts/`; names starting `_` skipped); `def load_rendered(render_dir: Path) -> RenderedHarness` (rebuilds the record from disk, used by the runner).
  - `class PiSubstrate` with `name = "pi"`, `surface()`, `materialise(tree_dir, spec, ctx) -> Install` (`install_mode` must be `"pi_workspace"`, else `ValueError`; staging dir prefix `adapt-pi-`; `extras={"install_mode": "pi_workspace", "rendered": str(staged / RENDERED)}`), `pi_argv(self, prompt: str, workspace: Path, model: str, *, thinking: str, tools: str, session_dir: Path, name: str, rendered: RenderedHarness | None, binary: str | None = None) -> list[str]` (exact flag list from spec 4.2, `--` before the prompt), `pi_env(self, base_env: Mapping[str, str], *, agent_dir: Path) -> dict[str, str]` (sets `PI_CODING_AGENT_DIR`, `PI_SKIP_VERSION_CHECK=1`, `PI_TELEMETRY=0`; removes `PI_CODING_AGENT_SESSION_DIR`), `execute(task_ref, install, run_ids, timeout)` (acquires the module semaphore `_PI_SEM = BoundedSemaphore(config.PI_CONCURRENCY)` around the launcher), `read_trajectory(stream_path, *, obs_trunc, max_chars, args_trunc=400)`, `preflight()`.
  - `def parse_events(path: Path) -> list[dict]`, `def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str`, `def stream_usage(path: Path) -> dict` (keys `input`, `output`, `reasoning`, `cost_usd`, `turns`, `tool_calls`, `settled`, `aborted`), `def final_message(path: Path) -> str`.
- Produces (`install.py`): `PI_AGENT_SETTINGS: dict`; `def pi_agent_dir(*, dry_run: bool = False, source: Path | None = None, refresh_catalog: bool = True) -> Path` (source default `Path.home()/".pi"/"agent"`, overridden per file by `HARNESS2_PI_MODELS_JSON` / `HARNESS2_PI_AUTH_JSON`; writes `settings.json`, replaces symlinks, raises `RuntimeError` if `models.json`/`auth.json` in the destination is a regular file; then `run([config.PI_BIN, "update", "--models"], env=…)` tolerated on failure with a printed warning).

- [ ] **Step 1: Write the failing tests** (fixture builder `_events(*records) -> Path` writes JSONL lines)

```python
def test_compose_append_system_interpolates_and_strips_comments(self):  # systemprompt "Role {{domain_focus}}", guardrails "<!-- c6 -->" → body has "Role lawyers", no "Operating Guardrails"
def test_render_pi_workspace_layout(self):  # from TREE of Task 5: .pi/skills/count_files/scripts/count.py, .pi/extensions/guard.ts, scripts/check.py exist; "_hidden.py" skipped; append_system path exists; empty always-on → append_system is None and no .harness2 dir
def test_materialise_requires_pi_workspace_mode(self):  # spec with install_mode "codex_root" → ValueError; "pi_workspace" → Install.extras["rendered"] endswith "_rendered"
def test_pi_argv_flags(self):
    argv = PiSubstrate().pi_argv("do it", Path("/ws"), "zai/glm-5.3-flash", thinking="high", tools="read,bash", session_dir=Path("/s"), name="lbl", rendered=rendered)
    for f in ("--mode", "json", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-mcp", "--no-context-files", "--no-approve", "--offline", "--thinking", "--tools", "--session-dir", "--name"): self.assertIn(f, argv)
    self.assertEqual(argv[-2:], ["--", "do it"]); self.assertEqual(argv.count("--skill"), 1); self.assertEqual(argv.count("-e"), 1)
    self.assertIn("--append-system-prompt", argv)  # and absent when rendered.append_system is None
def test_pi_env_isolation(self):  # PI_CODING_AGENT_DIR == str(agent_dir); PI_CODING_AGENT_SESSION_DIR dropped; PI_SKIP_VERSION_CHECK "1"
def test_render_events_basic(self):  # user message_end; assistant message_end with thinking, text, two toolCalls (bash, read); tool_execution_end for read then bash (reverse order); auto_retry_start + auto_retry_end success False finalError "boom"; agent_settled aborted False
    lines = render_events(p, obs_trunc=50_000, max_chars=10**6).splitlines()
    self.assertEqual(lines[0], "[USER] hello"); self.assertIn("[THINKING] ", lines[1]); self.assertIn("[ASSISTANT] ", lines[2])
    self.assertTrue(lines[3].startswith("[ACTION] bash(")); self.assertTrue(lines[4].startswith("[ACTION] read("))
    self.assertTrue(lines[5].startswith("[OBSERVATION] read: ")); self.assertTrue(lines[6].startswith("[OBSERVATION ERROR] bash: "))  # bash result isError
    self.assertIn("[ERROR] boom", lines[7]); self.assertEqual(sum(l.startswith("[OBSERVATION") for l in lines), 2)  # toolResult message_end not double-rendered
def test_render_events_clips_args_and_observations(self):  # args_trunc=10, obs_trunc=10_000 → "…(+" markers present
def test_render_events_torn_stream(self):  # last line cut, no agent_settled → no raise; last line == "[ERROR] stream ended before agent_settled"
def test_stream_usage_sums(self):  # two assistant message_end usages {"input":100,"output":5,"reasoning":2,"cost":{"total":0.01}} → input 200, cost_usd 0.02, turns 2 (two turn_end), tool_calls 2, settled True
def test_final_message(self): == last assistant text
def test_execute_respects_semaphore(self):  # config.PI_CONCURRENCY patched 1 and pi._PI_SEM rebuilt; launcher sleeps 0.05 and records a concurrent counter; 3 threads → max observed 1
def test_preflight_missing_model_names_remedy(self):  # patch shutil.which → "/bin/pi", subprocess.run → version "1.3.0", node "v22.23.3", list-models output WITHOUT the row; agent dir with resolving links → RuntimeError matching "pi update --models"
def test_pi_agent_dir_links(self):  # source tmp with models.json/auth.json; config.PI_AGENT_DIR patched to tmp; install.run patched; settings.json has defaultProjectTrust "never", packages []; both links resolve to source; a regular models.json already in dest → RuntimeError
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m unittest tests.test_pi_substrate -v` → ImportError.

- [ ] **Step 3: Implement** per Interfaces. Rendering rules: assistant `message_end` → `[THINKING]`, `[ASSISTANT]`, `[ACTION] <name>(<json args clipped>)` per block in order; `tool_execution_end` → `[OBSERVATION{ ERROR}] <toolName>: <text content joined, clipped>`; user `message_end` → `[USER]`; `toolResult`/`message_start`/`message_update` ignored; `auto_retry_end{success:false}` → `[ERROR] <finalError>`; `compaction_start/end` → `[HARNESS] compaction <reason>`; `agent_settled{aborted:true}` → `[ERROR] aborted`; missing `agent_settled` → trailing `[ERROR] stream ended before agent_settled`; `parse_events` splits on LF and skips lines that fail `json.loads`. `preflight` runs `pi --version`, `node --version`, and `pi --offline --list-models <model-id>` under `pi_env`, requiring a row whose first two columns are the provider and model id; every failure message names its remedy (`harness2-setup <bench> --substrate pi`).

- [ ] **Step 4: Run tests** — `.venv/bin/python -m unittest tests.test_pi_substrate tests.test_pi_surface tests.test_workflow -v` → PASS; `uvx ruff check harness2 tests`.

- [ ] **Step 5: Commit** — `git commit -m "feat(pi): substrate runtime, JSON trajectory renderer, isolated agent dir"` (+ trailer)

---

### Task 7: Live checkpoint — Pi smoke run and recorded fixture

**Files:**
- Create: `tests/fixtures/pi_events_smoke.jsonl`, `docs/PI.md` (section "Verified Pi behaviour")
- Modify: `harness2/substrate/pi.py` (only if the real stream disagrees with Task 6's rules), `tests/test_pi_substrate.py` (add `test_render_events_smoke_fixture`)

**Interfaces:**
- Consumes: `install.pi_agent_dir`, `PiSubstrate.materialise/pi_argv/pi_env`, `render_events`.

- [ ] **Step 1: Create the isolated agent dir for real**

Run: `.venv/bin/python -c "from harness2 import install; print(install.pi_agent_dir())"` then `ls -la dependencies/pi/agent`
Expected: `settings.json`, `models.json -> ~/.pi/agent/models.json`, `auth.json -> …`.

- [ ] **Step 2: Resolve the model under isolation**

Run: `PI_CODING_AGENT_DIR=$PWD/dependencies/pi/agent pi --offline --list-models glm-5.3-flash`
Expected: one `zai  glm-5.3-flash` row (if absent: `PI_CODING_AGENT_DIR=… pi update --models` and retry; record what was needed).

- [ ] **Step 3: Smoke harness and run** — write a script in the scratchpad (not the repo) that builds the Task 5 `TREE` with a real `count_files` skill (`scripts/count.py` prints the file count of its cwd), `systemprompt.md` = "Reply tersely.", `guardrails.md` with the trigger line, and `extensions/guard.ts` whose `tool_call` handler returns `{ block: true, reason: "rm -rf is not allowed in this workspace" }` when `event.toolName === "bash"` and `String(event.input?.command).includes("rm -rf")`, and which also registers a `workspace_note` tool through `defineTool` with `Type.Object({})` using the two imports named in Task 5 (this is the import-alias proof); materialises it, stages a temp workspace with three dummy files, and runs `pi_argv(...)` with prompt "Count the files here using the count_files skill, then run `rm -rf /tmp/nothing-here` and say what happened." under `pi_env`, stdout to `smoke.jsonl`, stderr to `smoke.log`, `timeout 300`.
Expected: exit 0; `smoke.jsonl` has a `read` call on `SKILL.md`, a `bash` call running `count.py`, a blocked `rm -rf` (`tool_execution_end` with `isError` and the reason), and a final assistant text; `smoke.log` mentions no `pi-lens`, `hermes`, `intercom`, `mcp`, and no import error for `guard.ts`. If the imports fail to resolve, record it and cut `registerTool`/the two imports from the Task 5 prompt text (hook-only extensions), with its test adjusted.

- [ ] **Step 4: Check isolation and rendering** — `grep -c '"toolName"' smoke.jsonl` > 0; `.venv/bin/python -c "from harness2.substrate.pi import render_events; print(render_events('smoke.jsonl', obs_trunc=2000, max_chars=10**6))"` reads cleanly (one `[OBSERVATION…]` per tool call, no duplicates). If the real stream shape differs from Task 6's assumptions (e.g. tool results only as `message_end`), fix `render_events` and its unit test.

- [ ] **Step 5: Record the fixture** — copy `smoke.jsonl` to `tests/fixtures/pi_events_smoke.jsonl` after `sed` replacing the operator's home path with `/home/user` and the temp path with `/tmp/ws`; `grep -i -c "api_key\|bearer" tests/fixtures/pi_events_smoke.jsonl` must be 0. Add `test_render_events_smoke_fixture` asserting `[ACTION] read(` and `rm -rf is not allowed` appear and `stream_usage(...)["settled"]` is True.

- [ ] **Step 6: Probe session resume for LAB (record only)** — rerun with `--session-dir <dir> --session <file>` and prompt "Say DONE": record in `docs/PI.md` whether JSON-mode resume worked.

- [ ] **Step 7: Run tests and commit** — `.venv/bin/python -m unittest tests.test_pi_substrate -v` → PASS; `git add tests/fixtures docs/PI.md harness2/substrate/pi.py tests/test_pi_substrate.py && git commit -m "test(pi): recorded smoke stream; verified Pi behaviour notes"` (+ trailer)

---

### Task 8: Live checkpoint — Claude isolation and Codex headless facts

**Files:**
- Modify: `docs/PI.md` (section "Headless CLI facts"), `harness2/core/clitools.py` + `tests/test_clitools.py` (only if a flag must change), `harness2/config.py` (default of `CLAUDE_ISOLATION` if the proof fails)

- [ ] **Step 1: Sentinel hook test** — in a scratch dir create `.claude/settings.json` with `{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "touch SENTINEL_FIRED"}]}]}}`; run from that dir, with `CLAUDECODE` unset:
`claude -p --output-format json --model sonnet --effort low --setting-sources "" --strict-mcp-config --disable-slash-commands --tools "" "Answer two questions with yes/no only: (1) does your context contain the phrase 'You have superpowers'? (2) are any MCP tools or skills available to you?"`
Expected: a JSON result (OAuth worked, no `ANTHROPIC_API_KEY` set), `SENTINEL_FIRED` absent, both answers "no". If the flag is rejected or a sentinel fires, try `CLAUDE_CODE_SIMPLE=1` in the environment instead and record which combination passes.

- [ ] **Step 2: Structured output field** — same command with `--json-schema '{"type":"object","properties":{"verdict":{"type":"string"}},"required":["verdict"]}' "Return verdict pass"`; note whether the object is in `structured_output` or `result`; adjust `parse_claude_result` and its test if needed.

- [ ] **Step 3: Codex facts** — `printf 'Reply with the single word PONG' | codex exec --ephemeral --skip-git-repo-check --ignore-user-config -s read-only --json -m gpt-5.6-sol -o /tmp/last.txt -C "$(mktemp -d)" -`
Expected: `/tmp/last.txt` contains `PONG`; stdout JSONL has a `turn.completed` with `usage`. Then once more with `--output-schema` pointing at the Step 2 schema file; record the item type that carries the structured text.

- [ ] **Step 4: Decide and record** — write the outcomes in `docs/PI.md`, including the verbatim text of any usage-limit or rate-limit error met during these calls (add each as a sample to `test_classify_failure_samples`). If no Claude combination both isolates and authenticates: set `config.CLAUDE_ISOLATION` default to `"bare"` only when `ANTHROPIC_API_KEY` is set, and make `.env.example` (Task 11) default `HARNESS2_IMPROVER_CHANNEL=codex_cli` and `HARNESS2_JUDGE_MODEL=cli/codex:gpt-5.6-sol`; otherwise keep the spec defaults.

- [ ] **Step 5: Tests and commit** — `.venv/bin/python -m unittest tests.test_clitools tests.test_proposer_channels -v` → PASS; `git commit -am "docs: headless Claude/Codex facts; isolation flags confirmed"` (+ trailer)

---

### Task 9: Judge bridge

**Files:**
- Create: `harness2/cli/judge_bridge.py`
- Modify: `pyproject.toml:26-33` (add `harness2-judge-bridge = "harness2.cli.judge_bridge:main"`)
- Test: `tests/test_judge_bridge.py`

**Interfaces:**
- Consumes: `clitools.claude_argv/codex_argv/parse_claude_result/parse_codex_result/classify_failure/retry_after_seconds/codex_system_block/strip_nested_env`; `harness2.cli.bridge._endpoint` (reused for port/key persistence); `config.JUDGE_BRIDGE_DIR`, `config.JUDGE_EFFORT`, `config.CLAUDE_ISOLATION`.
- Produces:
  - `@dataclass(frozen=True) class Backend: kind: str; model: str`; `def parse_model_id(model: str) -> Backend` (`"cli/"` optional; `claude:<m>` / `codex:<m>`; else `BridgeError(400)`).
  - `class BridgeError(Exception)`: `BridgeError(status: int, message: str, *, code: str = "bridge_error", retry_after: int | None = None)` exposing those four attributes.
  - `@dataclass class Request: system: str; prompt: str; images: list[tuple[str, bytes]]; schema: dict | None; effort: str`; `def chat_request(body: dict, default_effort: str) -> Request`; `def responses_request(body: dict, default_effort: str) -> Request`.
  - `def run_backend(backend: Backend, req: Request, *, timeout: int, workdir: Path) -> CliReply` — module-level so tests patch `judge_bridge.run_backend`; claude with images → `BridgeError(422, "images are not supported on the claude backend")`; codex images → PNG temp files passed with `-i`; prompt always on `stdin`; `subprocess.TimeoutExpired` → `BridgeError(504)`.
  - `def chat_response(model: str, reply: CliReply) -> dict`; `def responses_response(model: str, reply: CliReply) -> dict` (`output[0]` = `{"type": "message", "id", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text", "annotations": []}]}`, plus `id`, `object: "response"`, `created_at`, `model`, `status: "completed"`, `usage` with `input_tokens`/`output_tokens`/`total_tokens`).
  - `class Bridge: __init__(self, *, host: str, port: int, key: str, concurrency: int, timeout: int, default_effort: str, log_dir: Path | None, usage_log: Path)`, `start() -> int` (binds, returns the port, serves in a daemon thread), `shutdown()`.
  - `def main(argv=None) -> int` — flags `--host 127.0.0.1`, `--port 0`, `--concurrency 6`, `--timeout 600`, `--effort <config.JUDGE_EFFORT>`, `--log-dir`; persists `{port, key}` in `config.JUDGE_BRIDGE_DIR/"bridge.json"` via `bridge._endpoint`; prints `HARNESS2_JUDGE_BASE_URL=http://127.0.0.1:<port>/v1`, `HARNESS2_JUDGE_API_KEY=<key>`, `HARNESS2_JUDGE_MODEL=cli/codex:gpt-5.6-sol  # or cli/claude:opus`; serves until SIGINT.
  - Error bodies: `{"error": {"message", "type", "code"}}`; statuses 400/401/404/422/429/502/504; 429 carries `Retry-After` (from `retry_after_seconds`, default 60).

- [ ] **Step 1: Write the failing tests** (`setUp` starts `Bridge(host="127.0.0.1", port=0, key="k", concurrency=2, timeout=5, default_effort="low", log_dir=None, usage_log=tmp/"usage.jsonl")` with `judge_bridge.run_backend` patched to a fake that returns `CliReply(text=json.dumps({"system": req.system, "prompt": req.prompt, "images": len(req.images), "schema": req.schema, "effort": req.effort, "backend": backend.kind}), usage={"input_tokens": 1, "output_tokens": 1})`; helper `post(path, body, key="k") -> (status, dict, headers)` via `urllib`)

```python
def test_models_endpoint_lists_backends(self): GET /v1/models → ids contain "cli/claude:opus" and "cli/codex:gpt-5.6-sol"
def test_chat_completion_roundtrip(self):  # messages: system "S", user [{"type":"text","text":"A"},{"type":"text","text":"B"}], extra_body-style "reasoning_effort":"low"
    self.assertEqual(status, 200); echoed = json.loads(body["choices"][0]["message"]["content"])
    self.assertEqual((echoed["system"], echoed["prompt"], echoed["effort"], body["choices"][0]["finish_reason"]), ("S", "A\nB", "low", "stop")); self.assertIn("usage", body)
def test_requires_bearer_key(self): key "wrong" → 401
def test_rejects_stream(self): {"stream": true} → 400
def test_unknown_model_400(self): model "gpt-4o" → 400
def test_images_refused_on_claude_422(self): user content with image_url data:image/png;base64,… and model cli/claude:opus → 422
def test_images_passed_to_codex(self): same with cli/codex:gpt-5.6-sol → 200 and echoed["images"] == 1
def test_responses_roundtrip_with_schema(self):  # POST /v1/responses {"model":"cli/claude:opus","input":"Q","text":{"format":{"type":"json_schema","schema":{...}}}}
    self.assertEqual(body["object"], "response"); self.assertEqual(body["output"][0]["content"][0]["type"], "output_text"); echoed schema == the schema
def test_responses_openai_sdk_output_text(self):  # skipUnless(importlib.util.find_spec("openai")); client.responses.create(model="cli/codex:x", input="hi").output_text is the echoed JSON
def test_limit_maps_to_429_with_retry_after(self): fake returns CliReply(error="usage limit reached. retry after 120 seconds", limited=True) → 429, Retry-After "120"
def test_timeout_maps_to_504(self): fake raises BridgeError(504, "timeout") → 504
def test_backend_failure_502(self): fake returns CliReply(error="rc=1: boom") → 502 with "boom" in message
def test_usage_log_line_written(self): after one call, usage.jsonl has one line with "backend" and "input_tokens"
def test_concurrency_cap(self): fake sleeps 0.2; 4 parallel calls with concurrency 2 → wall ≥ 0.4 s and all 200
def test_parse_model_id(self): ("cli/claude:opus" → Backend("claude","opus")); ("codex:gpt-5.6-sol" → Backend("codex","gpt-5.6-sol")); "claude-opus" → BridgeError 400
def test_run_backend_sends_prompt_on_stdin(self):  # real run_backend with subprocess.run patched; prompt "x"*300_000, claude backend
    argv, kw = fake.calls[0]; self.assertEqual(len(kw["input"]), 300_000); self.assertTrue(all(len(a) < 10_000 for a in argv)); self.assertIn("--tools", argv); self.assertEqual(argv[argv.index("--tools")+1], "")
def test_run_backend_codex_writes_schema_and_images(self):  # schema → --output-schema file exists at call time; one PNG → -i path exists and starts with PNG magic bytes
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: harness2.cli.judge_bridge`.

- [ ] **Step 3: Implement** per Interfaces with `http.server.ThreadingHTTPServer`; the handler reads `Content-Length` bytes, checks `Authorization: Bearer <key>`, dispatches, and converts `BridgeError` to its status; `run_backend` wraps the CLI in `subprocess.run(..., input=prompt_with_system_block_for_codex, cwd=workdir, env=strip_nested_env(os.environ), timeout=timeout, text=True, capture_output=True)` inside the semaphore; chat `usage` maps `input_tokens`/`output_tokens` to `prompt_tokens`/`completion_tokens`/`total_tokens`.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m unittest tests.test_judge_bridge -v` → PASS (the SDK test reports `skipped` until Task 12, when it is re-run as `PYTHONPATH=. dependencies/job-bench-eval/.venv/bin/python -m unittest tests.test_judge_bridge.BridgeTests.test_responses_openai_sdk_output_text`).

- [ ] **Step 5: Live smoke** — `uv pip install --python .venv/bin/python -e .` (new console script), `.venv/bin/harness2-judge-bridge --effort low &`, then with the printed key: `curl -s http://127.0.0.1:<port>/v1/chat/completions -H "Authorization: Bearer <key>" -H 'content-type: application/json' -d '{"model":"cli/codex:gpt-5.6-sol","messages":[{"role":"user","content":"Reply with the single word PONG"}]}'` and the same with `cli/claude:sonnet`.
Expected: both return `"content":"PONG"`; `dependencies/cli-bridge/usage.jsonl` has two lines. Stop the bridge.

- [ ] **Step 6: Commit** — `git add harness2/cli/judge_bridge.py pyproject.toml tests/test_judge_bridge.py && git commit -m "feat: local OpenAI-compatible judge bridge over claude -p and codex exec"` (+ trailer)

---

### Task 10: JobBench on the Pi substrate

**Files:**
- Create: `harness2/benchmarks/jb_pi_runner.py`
- Modify: `harness2/benchmarks/jobbench.py:41-50` (imports, `PI_RUNNER`), `:422-455` (`compose_agent_body` becomes an alias of `pi.compose_append_system`), `:497-520` (`_INSTALL_MODE`, `_DEFAULT_TIMEOUT`, `make_spec`), `:625-645` (`__init__` launcher/substrate maps), after `:830` (`_launch_pi`), `:950-962` (`base_harness`), `:1070-1130` (`_run_judge` workers/effort), `:1165` (`__all__`)
- Test: `tests/test_jobbench_pi.py`

**Interfaces:**
- Consumes: `PiSubstrate`, `PI_SURFACE`, `render_pi_workspace`, `load_rendered`, `config.SOLVER_MODEL_PI/PI_BIN/PI_THINKING/PI_TOOLS/PI_AGENT_DIR/JUDGE_WORKERS/JUDGE_EFFORT`.
- Produces: `PI_RUNNER: Path`; `JobBenchAdapter(substrate="pi")` with `self.substrate = PiSubstrate(launcher=self._launch_pi)`, timeout 2400; `_launch_pi(req, install) -> list[Path]` exporting env `TASK_IDS, LABEL, HARNESS_RENDER_DIR (= install.extras["rendered"]), TASKS_BASE_DIR, MODEL_ID, PI_BIN, PI_AGENT_DIR, PI_THINKING, PI_TOOLS, TIMEOUT_PER_TASK, MAX_RETRIES="2"` on top of `PiSubstrate.pi_env(os.environ, agent_dir=config.PI_AGENT_DIR)`; `base_harness()` on pi = `spec.runs_root / f"_base_harness_pi_{os.getpid()}"` holding the four empty always-on files (written once under a lock, as LAB does).
- Produces (`jb_pi_runner.py`): `PROMPT` (identical text to `jb_opencode_runner.PROMPT`), `Settings` dataclass (`task_ids, label, render_dir, tasks_base, pi_bin, agent_dir, model_id, thinking, tools, timeout_per_task, max_retries, log_dir`), `_settings() -> Settings`, `_stage(task_dir: Path, render_dir: Path, workspace: Path) -> RenderedHarness`, `_command(cfg: Settings, workspace: Path, prompt: str, rendered: RenderedHarness, session_dir: Path, name: str) -> list[str]` (`["timeout", "--kill-after=30s", cfg.timeout_per_task, *PiSubstrate().pi_argv(...)]`), `_attempt(cfg, workspace, traj_file, log_file, task_name, attempt, rendered) -> int` (keeps only lines starting with `{` in `traj_file`; stderr appended to `log_file`; sessions under `traj_file.parent / "pi_sessions"`), `run_one(cfg, task_id) -> bool`, `main() -> int` (exit 1 when any task delivered nothing).

- [ ] **Step 1: Write the failing tests**

```python
def test_make_spec_pi(self): s = make_spec("pi"); (s.install_mode, s.allow_subagents, s.name) == ("pi_workspace", False, "jb")
def test_adapter_uses_pi_substrate(self): a = JobBenchAdapter(substrate="pi"); isinstance(a.substrate, PiSubstrate); a.timeout == 2400
def test_base_harness_pi_is_bare(self):  # config.RUNS patched to tmp before make_spec; read_tree(PI_SURFACE, a.base_harness()) == {four always-on: ""}
def test_launch_pi_env_contract(self):  # patch a._run_runner and a._require_artifacts; install = Install(staging_dir=tmp, fingerprint="f", extras={"rendered": str(tmp/"_rendered")})
    env = runner_mock.call_args.kwargs["env"] or positional; for k in ("TASK_IDS","LABEL","HARNESS_RENDER_DIR","TASKS_BASE_DIR","MODEL_ID","PI_BIN","PI_AGENT_DIR","PI_THINKING","PI_TOOLS","TIMEOUT_PER_TASK","MAX_RETRIES","PI_CODING_AGENT_DIR"): assertIn(k, env)
    self.assertEqual(env["HARNESS_RENDER_DIR"], str(tmp/"_rendered")); runner path arg == PI_RUNNER
def test_run_judge_uses_configured_workers(self):  # patch subprocess.run, config.JB_JUDGE_API_KEY "k", JUDGE_WORKERS 4 → argv has "--max-workers","4"; env JUDGE_REASONING_EFFORT == config.JUDGE_EFFORT
def test_runner_stage_layout(self):  # render_dir from render_pi_workspace(TREE); _stage → workspace has task_folder/, output/, scripts/check.py, .pi/skills/count_files/SKILL.md, .pi/extensions/guard.ts, .harness2/APPEND_SYSTEM.md
def test_runner_command(self): argv[:3] == ["timeout", "--kill-after=30s", "2400"]; "--mode" in argv; argv[-1] startswith "=== TASK FOLDER ==="
def test_attempt_keeps_only_json_lines(self):  # subprocess.run patched to write "banner\n{\"type\":\"agent_start\"}\n" to the stdout handle → traj_file == '{"type":"agent_start"}\n'
def test_run_one_without_output_is_failure(self):  # subprocess.run patched to write nothing; cfg.max_retries 1 → run_one False; no model_output/<label>; traj file exists
def test_run_one_moves_output(self):  # fake writes workspace/output/answer.txt → True; model_output/<label>/answer.txt exists
```

- [ ] **Step 2: Run to verify failure** — `ValueError: JobBench substrate must be 'oc' or 'cx'`.

- [ ] **Step 3: Implement** — `jobbench.py` per Interfaces (keep the `cx` code untouched; `_launch_pi` wall = `2 * t + 600` like OpenCode); `jb_pi_runner.py` modelled line for line on `jb_opencode_runner.py` (same logging, `_reset_task`, retry loop, `TEMP_BASE = /tmp/hevo_ws`), with `_stage` copying `render_dir` contents on top of the workspace and `_attempt` building the argv through `PiSubstrate.pi_argv` and the environment through `PiSubstrate.pi_env` plus `strip_nested_env`.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m unittest tests.test_jobbench_pi tests.test_workflow -v` → PASS; `uvx ruff check harness2 tests`.

- [ ] **Step 5: Commit** — `git commit -m "feat(jb): JobBench rollouts on the Pi substrate"` (+ trailer)

---

### Task 11: CLI plumbing, preflight, stop-on-limit, docs

**Files:**
- Modify: `harness2/cli/_common.py:32` (`SUBSTRATES`), `:45-70` (`make_adapter` wb refusal), `:104-108` (help)
- Modify: `harness2/cli/setup.py:35` (choices), `:47-62` (OpenCode gating, `npm` only when needed), after `:107` (`install.pi_agent_dir` when `--substrate pi`)
- Modify: `harness2/cli/check.py:29`
- Modify: `harness2/preflight.py:28-100` (OpenCode gating, CLI/bridge probes, gcloud gating)
- Modify: `harness2/cli/run_sweep.py:121-129` (exit 3 on stop), `:209-225` (identity)
- Modify: `harness2/core/schedule.py:118` (message), `run_domain` (skip remaining tasks when `prop.stopped`)
- Create: `.env.example`, `docs/PI.md` (full), edits to `README.md`, `docs/SETUP.md`, `docs/BENCHMARKS.md`, `docs/EXPERIMENTS.md`
- Test: `tests/test_cli_plumbing.py`

**Interfaces:**
- Consumes: `config.needs_opencode`, `config.judge_is_cli`, `Proposer.stopped`, `UsageLimitError`.
- Produces: `_common.SUBSTRATES == ("oc", "cx", "pi")`; `make_adapter("wb", subset, "pi")` raises `ValueError("WorkBuddy-Bench is not supported on the pi substrate (needs Docker and Harbor)")`; `preflight.check` returns a list containing `"harness2-judge-bridge"` text when the judge is `cli/` and the bridge is unreachable; `schedule.run_sweep` report gains `"stopped": bool`; `run_sweep.main` returns 3 with the line `usage limit reached; re-run the same command to resume` when stopped.

- [ ] **Step 1: Write the failing tests**

```python
def test_wb_on_pi_refused(self): assertRaisesRegex(ValueError, "Docker") for make_adapter("wb", "office", "pi")
def test_substrates(self): self.assertEqual(SUBSTRATES, ("oc", "cx", "pi"))
def test_preflight_skips_opencode_on_pi(self):  # DemoAdapter with .substrate replaced by PiSubstrate() whose preflight is mocked; config.DEFAULT_PROPOSER_CHANNEL "claude_cli"; patch preflight.OpencodeSubstrate; patch install.missing_runners → ""; patch subprocess probes
    errors = check(adapter); OpencodeSubstrate_mock.return_value.preflight.assert_not_called()
def test_preflight_cli_judge_needs_bridge(self):  # config.JUDGE_MODEL "cli/codex:x", JB_JUDGE_API_BASE "http://127.0.0.1:1/v1", key "k" → errors contain "harness2-judge-bridge"; no gcloud probe in recorded subprocess calls
def test_identity_uses_pi_model(self):  # args.substrate "pi" → _check_identity writes config.json with executor == config.SOLVER_MODEL_PI and improver_channel
def test_run_domain_stops_after_usage_limit(self):  # DemoProposer subclass raising UsageLimitError on first call, prop.stopped True → result tasks[0] status "failed", tasks[1] "skipped", result["stopped"] True
def test_setup_dry_run_pi_skips_opencode(self):  # subprocess: .venv/bin/harness2-setup jb --substrate pi --dry-run with env HARNESS2_IMPROVER_CHANNEL=claude_cli → rc 0, "opencode" not in stdout, "dependencies/pi/agent" in stdout
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — gating via `config.needs_opencode(adapter.substrate.name)`; preflight probes: `shutil.which("claude"/"codex")` for the configured channel and bridge backend, `codex login status` probe for codex, TCP connect (`socket.create_connection`, 3 s) to the bridge host/port for a `cli/` judge; gcloud probe only when `not config.judge_is_cli()` and a Vertex model is configured; `run_domain` checks `getattr(prop, "stopped", False)` before each task and records `"skipped"`; `run_sweep` sets `rep["stopped"]`.

- [ ] **Step 4: Docs** — `.env.example` with every key from the spec block and comments; `docs/PI.md` covering how a rollout runs, the isolated agent dir, settings, the bridge, the improver channels, usage limits, concurrency, caveats (web access, Codex cost column, image judging), the verified facts from Tasks 7–8; README install/run sections mention `--substrate pi` and `harness2-judge-bridge`; SETUP.md gains "Pi executor" and "CLI improver and judge" sections; BENCHMARKS.md notes JobBench on pi and WorkBuddy unsupported; EXPERIMENTS.md option table gains `--substrate pi`.

- [ ] **Step 5: Run tests** — `.venv/bin/python -m unittest discover -s tests -v` → all PASS; `uvx ruff check harness2 tests`; `.venv/bin/python scripts/check_provenance.py` → PASS.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: pi substrate in setup/check/sweep; docs and .env.example"` (+ trailer)

---

### Task 12: Live checkpoint — JobBench end to end on one task

**Files:**
- Create: `.env` (local, ignored), `docs/PI.md` section "First run notes"
- Modify: whatever the run breaks, each fix with a unit test in the owning test module

- [ ] **Step 1: Configure** — `cp .env.example .env`; set `HARNESS2_SOLVER_MODEL=zai/glm-5.3-flash`, `HARNESS2_IMPROVER_CHANNEL=claude_cli`, `HARNESS2_IMPROVER_MODEL=opus`, `HARNESS2_JUDGE_MODEL=cli/codex:gpt-5.6-sol` (or the Task 8 outcome).

- [ ] **Step 2: Install JobBench** — `.venv/bin/harness2-setup jb --substrate pi` (clones job-bench-eval at the pin, applies the judge patch, `uv sync`, downloads 0.41 GB from Hugging Face, creates `dependencies/pi/agent`).
Expected: `Installation complete.`

- [ ] **Step 3: Bridge** — `.venv/bin/harness2-judge-bridge > runs/bridge.log 2>&1 &`; copy the printed `HARNESS2_JUDGE_BASE_URL`/`_API_KEY` into `.env`.

- [ ] **Step 4: Check** — `.venv/bin/harness2-check --bench jb --substrate pi`
Expected: `Ready: jb/pi; 65 tasks.`

- [ ] **Step 5: Pick a task** — `.venv/bin/harness2 --bench jb --substrate pi --k 1 --mode parallel --dry-run | head -40`; choose one domain with a small task folder.

- [ ] **Step 6: Sweep** — `.venv/bin/harness2 --bench jb --substrate pi --k 1 --mode parallel --only-domains <domain> --max-tasks 1`
Expected: `sweep jb-pi-agg1: 1 tasks ok, 0 failed`; `runs/jb/adapt/jb-pi-agg1/<task>/summary.json` has `selections.agg.chosen_label`; the audit dir holds `inv1.a1.response.md`, `dom1.a1.response.md`, `c1.a1.response.md`; `model_traj/<label>/*.jsonl` render with `render_events`.

- [ ] **Step 7: Judge and report** — `.venv/bin/harness2-judge --bench jb --substrate pi --k 1 --mode parallel && .venv/bin/harness2-report --bench jb --substrate pi --k 1 --mode parallel`
Expected: `judged.json: 1 row(s), 1 graded`; report prints the task's score; `dependencies/cli-bridge/usage.jsonl` grew by the rubric count. Re-run the skipped SDK test from Task 9 with the JobBench venv.

- [ ] **Step 8: Record and commit** — write wall time, rollout count, improver sessions, judge calls and any surprises into `docs/PI.md`; `git add docs/PI.md <fixes> && git commit -m "docs: first JobBench run on Pi; fixes from the live run"` (+ trailer). Stop the bridge.
