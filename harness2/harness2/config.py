# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deployment settings and dependency versions. Shell values override .env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent if (PKG.parent / "pyproject.toml").is_file() else Path.cwd()
THIRD_PARTY = ROOT / "third_party"
METHOD_VERSION = 2

DEPENDENCIES = ROOT / "dependencies"


class ConfigError(RuntimeError):
    """A required deployment value is missing; the message carries the remediation."""


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from `path` into os.environ with setdefault (the shell wins).

    Accepts blank lines, `#` comments, an optional `export ` prefix, and single- or
    double-quoted values. A missing or unreadable file is not an error.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not all(c.isalnum() or c == "_" for c in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


load_dotenv()


def env(name: str, default=None):
    """Env override where a set-but-empty variable is the same as an unset one."""
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    v = env(name)
    if v is None:
        return int(default)
    try:
        return int(v)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {v!r}") from e


def require(name: str, *, needed_by: str) -> str:
    """The value of `name`, or ConfigError with the remediation line when it is unset/empty."""
    v = env(name)
    if not v:
        raise ConfigError(
            f"{name} is required for {needed_by}; set it in .env or the shell (see .env.example)"
        )
    return str(v)


RUNS = Path(env("HARNESS2_RUNS_DIR", ROOT / "runs")).expanduser().resolve()
VERTEX_PROJECT = env("GOOGLE_CLOUD_PROJECT", env("HARNESS2_VERTEX_PROJECT", ""))
VERTEX_REGION = env("GOOGLE_CLOUD_LOCATION", "global")
if VERTEX_PROJECT:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", VERTEX_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", VERTEX_REGION)


SOLVER_MODEL_OC = env("HARNESS2_SOLVER_MODEL", "")
SOLVER_MODEL_CX = env("HARNESS2_CODEX_MODEL", SOLVER_MODEL_OC.split("/", 1)[-1])
IMPROVER_MODEL = env("HARNESS2_IMPROVER_MODEL", SOLVER_MODEL_OC)
JUDGE_MODEL = env("HARNESS2_JUDGE_MODEL", "")

# Pi substrate: the solver id uses the same provider/model form as OpenCode.
SOLVER_MODEL_PI = SOLVER_MODEL_OC
PI_BIN = env("HARNESS2_PI_BIN", "pi")
PI_THINKING = env("HARNESS2_PI_THINKING", "high")
# Pi's additive form (+name) keeps the default read/bash/edit/write AND every tool a harness
# extension registers; a plain allowlist would silently drop extension tools.
PI_TOOLS = env("HARNESS2_PI_TOOLS", "+grep,+find,+ls")
PI_CONCURRENCY = env_int("HARNESS2_PI_CONCURRENCY", 2)
PI_AGENT_DIR = DEPENDENCIES / "pi" / "agent"

# Improver channel (proposer, composer, selector, reflector) and plan-limit policy.
IMPROVER_CHANNEL = env("HARNESS2_IMPROVER_CHANNEL", "opencode")
IMPROVER_EFFORT = env("HARNESS2_IMPROVER_EFFORT", "high")
CLAUDE_USE_VERTEX = env("HARNESS2_CLAUDE_USE_VERTEX", "0") == "1"
CLAUDE_ISOLATION = env("HARNESS2_CLAUDE_ISOLATION", "flags")
LIMIT_MAX_WAIT = env_int("HARNESS2_LIMIT_MAX_WAIT", 7200)
STOP_ON_LIMIT = env("HARNESS2_STOP_ON_LIMIT", "0") == "1"

# Judge served through harness2-judge-bridge when HARNESS2_JUDGE_MODEL starts with "cli/".
JUDGE_EFFORT = env("HARNESS2_JUDGE_EFFORT", "low")
JUDGE_WORKERS = env_int("HARNESS2_JUDGE_WORKERS", 6)
# Bridge-served judges can be slow under plan rate limits: generous client-side timeouts.
JUDGE_RUBRIC_TIMEOUT = env_int("HARNESS2_JUDGE_RUBRIC_TIMEOUT", 600)
JUDGE_TIMEOUT = env_int("HARNESS2_JUDGE_TIMEOUT", 3600)
JUDGE_BRIDGE_DIR = DEPENDENCIES / "cli-bridge"

DEPENDENCY_PINS = {
    "job-bench-eval": "fa03a17f9ff662f136798a274e10011c3aa00fd0",
    "workbuddy-bench": "b516950be5b56eb3be406c2f76ee1c5111dcb57f",
    "harvey-labs": "b58a28ae9fde5ee4b810e53b4fd0a1915325a35d",
    "opencode": "23fb5e0516c99ac04a1aa46c193efda2e1b9bb24",
}
DEPENDENCY_ORIGINS = {
    "job-bench-eval": "https://github.com/Job-Bench/job-bench-eval.git",
    "workbuddy-bench": "https://github.com/Tencent/workbuddy-bench.git",
    "harvey-labs": "https://github.com/harveyai/harvey-labs.git",
    "opencode": "https://github.com/anomalyco/opencode.git",
}


def jb_repo() -> Path:
    return DEPENDENCIES / "job-bench-eval"


def wb_repo() -> Path:
    return DEPENDENCIES / "workbuddy-bench"


def lab_repo() -> Path:
    return DEPENDENCIES / "harvey-labs"


OPENCODE_DIR = DEPENDENCIES / "opencode"
BUN = DEPENDENCIES / "tools" / "node_modules" / ".bin" / "bun"
LAB_PYTHON = lab_repo() / ".venv" / "bin" / "python"
LAB_JUDGE_MODEL = (
    "vertex/" + JUDGE_MODEL if JUDGE_MODEL.startswith(("claude", "gemini")) else JUDGE_MODEL
)
LAB_DELIV_CAP = 60000
JB_JUDGE_PYTHON = jb_repo() / ".venv" / "bin" / "python"
JB_JUDGE_MODEL = (
    "google/" + JUDGE_MODEL
    if JUDGE_MODEL.startswith("gemini") and not env("HARNESS2_JUDGE_BASE_URL")
    else JUDGE_MODEL
)
_vertex_host = (
    "aiplatform.googleapis.com"
    if VERTEX_REGION == "global"
    else f"{VERTEX_REGION}-aiplatform.googleapis.com"
)
VERTEX_OPENAI_URL = (
    f"https://{_vertex_host}/v1/projects/{VERTEX_PROJECT}"
    f"/locations/{VERTEX_REGION}/endpoints/openapi"
)
JB_JUDGE_API_BASE = env("HARNESS2_JUDGE_BASE_URL", VERTEX_OPENAI_URL if VERTEX_PROJECT else "")
JB_JUDGE_API_KEY = env("HARNESS2_JUDGE_API_KEY", "")
JB_JUDGE_REASONING_EFFORT = JUDGE_EFFORT
JB_REPORTING_JUDGE_DETAILS = ""
JB_OBS_TRUNC = 50000

CODEX_VERSION = "0.144.6"
CODEX_DIST = str(DEPENDENCIES / "codex" / "dist")
CODEX_BASE_URL = env("HARNESS2_CODEX_BASE_URL", "")
CODEX_CONTAINER_URL = env("HARNESS2_CODEX_CONTAINER_URL", CODEX_BASE_URL)
VERTEX_PROXY_KEY = env("HARNESS2_CODEX_API_KEY", "")
CODEX_HOME_DIR = DEPENDENCIES / "codex" / "home"

WB_SOLVER_MODEL = "harness2/solver"
WB_WEB_JUDGE = "harness2/judge"
WB_RENDER_PY = sys.executable
WB_OC_HARNESS_VER = "1.14.18"
WB_CX_HARNESS_VER = CODEX_VERSION
WB_MAX_JOBS = {"oc": 1, "cx": 1}
WB_MAX_TRIALS = {"oc": 2, "cx": 2}
WB_MODEL_CONNECTION = "local_proxy"

DEFAULT_PROPOSER_CHANNEL = IMPROVER_CHANNEL
PROPOSER_MODEL_BY_CHANNEL = {
    "opencode": IMPROVER_MODEL,
    "claude_cli": IMPROVER_MODEL,
    "codex_cli": IMPROVER_MODEL,
}
PHASE_WIDTH = 2
TRANSPORT_ATTEMPTS = 2
PROPOSER_CONCURRENCY = 2


def needs_opencode(substrate: str, channel: str | None = None) -> bool:
    """Whether the OpenCode checkout is needed: as the executor, or as the improver channel."""
    return substrate == "oc" or (channel or DEFAULT_PROPOSER_CHANNEL) == "opencode"


def judge_is_cli() -> bool:
    """Whether the judge is served by harness2-judge-bridge (a `cli/` model id)."""
    return JUDGE_MODEL.startswith("cli/")


def validate_models(*, judge: bool = False) -> None:
    require("HARNESS2_SOLVER_MODEL", needed_by="executor rollouts")
    if "/" not in SOLVER_MODEL_OC:
        raise ConfigError(
            "HARNESS2_SOLVER_MODEL must use provider/model syntax (e.g. zai/glm-5.3-flash)"
        )
    if DEFAULT_PROPOSER_CHANNEL == "opencode":
        if "/" not in IMPROVER_MODEL:
            raise ConfigError(
                "The opencode improver channel needs a provider/model id in HARNESS2_IMPROVER_MODEL"
            )
    elif "/" in IMPROVER_MODEL:
        raise ConfigError(
            "claude_cli/codex_cli take a bare model id such as opus or gpt-5.6-sol in "
            "HARNESS2_IMPROVER_MODEL"
        )
    if any(m.startswith("google-vertex") for m in (SOLVER_MODEL_OC, IMPROVER_MODEL)):
        if not VERTEX_PROJECT:
            raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Vertex AI")
    if judge:
        require("HARNESS2_JUDGE_MODEL", needed_by="benchmark judging")
        if judge_is_cli() and not (JB_JUDGE_API_BASE and JB_JUDGE_API_KEY):
            raise ConfigError(
                "A cli/ judge needs HARNESS2_JUDGE_BASE_URL and HARNESS2_JUDGE_API_KEY; start "
                "harness2-judge-bridge and copy the lines it prints into .env"
            )
