# Modifications to Harness²

This folder is a modified copy of Google's open-source Harness² (Xu et al., 2026, "Recursive Agent Harnessing for an
Open World", arXiv 2610.14224), licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)). The upstream
source was imported on 2026-10-30 as a snapshot without its history (the first commit of this folder's history). Every
later commit here is Light of Baldr LLC's, made between 2026-10-30 and 2026-11-01. These changes and additions are
provided under the same Apache License, Version 2.0. Google's copyright and license notices are retained unchanged.

## What we changed
- **A Mojo benchmark.** Glimmer (Muse-Glimmer-30B, served by the Nomos kernel) runs as the executor inside the Pi
  coding agent, sandboxed with bwrap behind a broker that caps model calls. Compiling and running each task's tests
  grades it.
- **A capped Muse Wonder judge** (Meta Model API) for code-quality scores, kept separate from the pass/fail grade.
- **Improver isolation.** Claude improvers run in a sandbox behind a credential broker, with explicit auth modes and
  audit records, plus a restricted Claude Code login-tools improver.
- **An opt-in sampling preset** ("vendor", Glimmer's own defaults) for repeated held-out evaluation.
- **Host-side steering** and a pinned opener for Glimmer rollouts, including whole-context steering.
- **Fixes:** login isolation, fallback-only sweeps, failed-rollout evidence and broker diagnostics.

## Modified upstream files
Each carries the notice "Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md."

- `.env.example`
- `.gitignore`
- `README.md`
- `docs/PI.md`
- `docs/SNAPSHOT-IMPORT.md`
- `harness2/cli/_common.py`
- `harness2/cli/check.py`
- `harness2/cli/judge.py`
- `harness2/cli/judge_bridge.py`
- `harness2/cli/run_sweep.py`
- `harness2/core/clitools.py`
- `harness2/core/prompts.py`
- `harness2/core/proposer.py`
- `harness2/core/recursion.py`
- `harness2/core/schedule.py`
- `harness2/core/spec.py`
- `harness2/preflight.py`
- `pyproject.toml`
- `scripts/check_snapshot.py`
- `tests/test_clitools.py`
- `tests/test_judge_bridge.py`
- `tests/test_proposer_channels.py`

## Files added by Light of Baldr LLC
- `MANIFEST.in`
- `docs/CC-SESSION-IMPROVER.md`
- `docs/IMPROVER-AUTH-DECISION.md`
- `docs/IMPROVER-SANDBOX.md`
- `docs/MOJO.md`
- `harness2/benchmarks/mojo.py`
- `harness2/benchmarks/mojo_grade.py`
- `harness2/benchmarks/mojo_limits.py`
- `harness2/benchmarks/mojo_pi_runner.py`
- `harness2/benchmarks/mojo_tasks.py`
- `harness2/cli/mojo.py`
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
- `harness2/substrate/sandbox.py`
- `harness2/substrate/sandbox_entry.py`
- `scripts/check_cc_login_tools_live.py`
- `scripts/check_mojo_followups_offline.py`
- `tests/test_fallback_status.py`
- `tests/test_improver_api_key.py`
- `tests/test_improver_broker.py`
- `tests/test_improver_key_file.py`
- `tests/test_improver_namespace.py`
- `tests/test_improver_retention.py`
- `tests/test_improver_sandbox.py`
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
- `tests/test_sandbox_mojo.py`

The full history of these changes is in this repository's git log.
