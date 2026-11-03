# Modifications to Harness²

This folder is a modified copy of Google's open-source Harness² (Xu et al., 2026, "Recursive Agent Harnessing for an
Open World", arXiv 2610.14224), licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)). Our changes and
additions are provided under the same license. Google's copyright and license notices are retained unchanged.

## Provenance
- **Upstream:** Google's commit `1cfe254` (2026-10-13, "Fix arXiv link"). Every list below compares against it.
- **Before the hackathon, 2026-10-20:** 23 of our commits on top of `1cfe254`. They made Pi a substrate for Harness²
  (JobBench and LAB rollouts in Pi), added headless Claude and Codex CLI tooling with new proposer channels, and a local
  OpenAI-compatible judge bridge.
- **The import, 2026-10-30:** that tree (`75f8d20`) was imported as one snapshot, without its history. This folder's
  git history therefore starts after the 2026-10-20 changes. See [docs/SNAPSHOT-IMPORT.md](docs/SNAPSHOT-IMPORT.md).
- **In the hackathon window, 2026-10-30 to 2026-11-01:** the feature commits in this folder's history.
- **Release preparation, 2026-11-03:** for this public copy, path portability, licenses, these notices, the redactions
  below, and the provenance check's rule for notice metadata.

## What we changed
- **Pi as a substrate** (2026-10-20): JobBench and LAB rollouts in the Pi coding agent, a JSON trajectory renderer, a
  rollout env allowlist, headless Claude and Codex CLI channels for the proposer, and a local judge bridge.
- **A Mojo benchmark** (window): Glimmer (Muse-Glimmer-30B, served by the Nomos kernel) runs as the executor inside Pi,
  sandboxed with bwrap and no network, behind a broker that caps model calls. Compiling and running each task's tests
  grades it.
- **A capped Muse Wonder judge** (window, Meta Model API) for code-quality scores, kept separate from the pass/fail grade.
- **Improver isolation** (window): Claude improvers run in a sandbox behind a credential broker, with explicit auth modes
  and audit records, plus a restricted Claude Code login-tools improver.
- **An opt-in sampling preset** ("vendor", Glimmer's own defaults) for repeated held-out evaluation (window).
- **Host-side steering** and a pinned opener for Glimmer rollouts, including whole-context steering (window).

## Modified upstream files
Each carries the notice "Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md.", in a form its format allows: a comment, a Markdown footer, the first line of
`third_party/patches/harvey-labs-judge-vertex-anthropic.diff` (`git apply` skips text before the first diff header; applied to harvey-labs at its pinned commit, the
noticed patch gives the same result as before), or the `_notice` field of `dependencies/ASSETS.json`, which
`scripts/check_provenance.py` skips. That patch's pinned SHA-256 in `dependencies/ASSETS.json` was updated to match its new bytes
(`31bc81c3bee8…` before, `40ca2b22d0e0…` now).

- `README.md`
- `dependencies/ASSETS.json`
- `dependencies/PINNED.md`
- `docs/BENCHMARKS.md`
- `docs/EXPERIMENTS.md`
- `docs/SETUP.md`
- `harness2/benchmarks/jobbench.py`
- `harness2/benchmarks/lab.py`
- `harness2/cli/_common.py`
- `harness2/cli/check.py`
- `harness2/cli/judge.py`
- `harness2/cli/run_sweep.py`
- `harness2/cli/setup.py`
- `harness2/config.py`
- `harness2/core/prompts.py`
- `harness2/core/proposer.py`
- `harness2/core/recursion.py`
- `harness2/core/schedule.py`
- `harness2/core/spec.py`
- `harness2/install.py`
- `harness2/preflight.py`
- `harness2/substrate/__init__.py`
- `pyproject.toml`
- `scripts/check_provenance.py`
- `third_party/patches/harvey-labs-judge-vertex-anthropic.diff`

## Files added by Light of Baldr LLC
- `.env.example`
- `.gitignore`
- `docs/CC-SESSION-IMPROVER.md`
- `docs/IMPROVER-AUTH-DECISION.md`
- `docs/IMPROVER-SANDBOX.md`
- `docs/MOJO.md`
- `docs/PI.md`
- `docs/SNAPSHOT-IMPORT.md`
- `docs/superpowers/plans/2026-10-20-lab-on-pi.md`
- `docs/superpowers/plans/2026-10-20-pi-substrate-jobbench.md`
- `docs/superpowers/specs/2026-10-20-pi-substrate-cli-improver-judge-design.md`
- `harness2/benchmarks/jb_pi_runner.py`
- `harness2/benchmarks/lab_pi_runner.py`
- `harness2/benchmarks/mojo.py`
- `harness2/benchmarks/mojo_grade.py`
- `harness2/benchmarks/mojo_limits.py`
- `harness2/benchmarks/mojo_pi_runner.py`
- `harness2/benchmarks/mojo_tasks.py`
- `harness2/cli/judge_bridge.py`
- `harness2/cli/mojo.py`
- `harness2/core/clitools.py`
- `harness2/core/muse_judge.py`
- `harness2/substrate/improver.py`
- `harness2/substrate/improver_audit.py`
- `harness2/substrate/improver_auth.py`
- `harness2/substrate/improver_broker.py`
- `harness2/substrate/improver_entry.py`
- `harness2/substrate/improver_files.py`
- `harness2/substrate/login_tools.py`
- `harness2/substrate/login_tools_control.py`
- `harness2/substrate/login_tools_entry.py`
- `harness2/substrate/login_tools_files.py`
- `harness2/substrate/login_tools_server.py`
- `harness2/substrate/model_broker.py`
- `harness2/substrate/pi.py`
- `harness2/substrate/pi_guard.ts`
- `harness2/substrate/sandbox.py`
- `harness2/substrate/sandbox_entry.py`
- `scripts/check_cc_login_tools_live.py`
- `scripts/check_mojo_followups_offline.py`
- `scripts/check_snapshot.py`
- `tests/fixtures/pi_events_smoke.jsonl`
- `tests/test_check_provenance.py`
- `tests/test_cli_plumbing.py`
- `tests/test_clitools.py`
- `tests/test_config_channels.py`
- `tests/test_fallback_status.py`
- `tests/test_improver_api_key.py`
- `tests/test_improver_broker.py`
- `tests/test_improver_key_file.py`
- `tests/test_improver_namespace.py`
- `tests/test_improver_retention.py`
- `tests/test_improver_sandbox.py`
- `tests/test_jobbench_pi.py`
- `tests/test_judge_bridge.py`
- `tests/test_lab_patch.py`
- `tests/test_lab_pi.py`
- `tests/test_lab_pi_runner.py`
- `tests/test_login_tools_client.py`
- `tests/test_login_tools_control.py`
- `tests/test_login_tools_files.py`
- `tests/test_login_tools_server.py`
- `tests/test_model_broker_errors.py`
- `tests/test_mojo_followups.py`
- `tests/test_mojo_grade.py`
- `tests/test_mojo_judge.py`
- `tests/test_mojo_sampling.py`
- `tests/test_mojo_steer_opener.py`
- `tests/test_mojo_tasks.py`
- `tests/test_mojo_workflow.py`
- `tests/test_pi_substrate.py`
- `tests/test_pi_surface.py`
- `tests/test_proposer_channels.py`
- `tests/test_sandbox_mojo.py`

## Redactions in this public copy
- `docs/SNAPSHOT-IMPORT.md`: a sentence naming a non-project author identity was replaced (in its history, too).
- `docs/superpowers/specs/2026-10-20-pi-substrate-cli-improver-judge-design.md`: a private LAN address was replaced (in
  its history, too).
- Home-directory paths in code and docs became environment variables or `~/` paths (from 2026-11-03 on).

## Third-party components
`third_party/` keeps the upstream third-party licenses and provenance files unchanged. Some are not Apache-2.0: Tencent's
WorkBuddy Bench license (`third_party/licenses/workbuddy-bench.LICENSE`, also `third_party/wb_runner/LICENSE`) adds a
territorial clause saying WorkBuddy Bench is not intended for use within the European Union. Read each component's
license before using the corresponding benchmark.
