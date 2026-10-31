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

"""Improver sessions with file access in an isolated context workspace.

OpenCode reads output/answer.md or the final message; the Claude and Codex CLIs read the
final message. Prompts use stdin, and empty replies raise an error. A plan usage limit is
waited out (up to config.LIMIT_MAX_WAIT) before it counts as a failed attempt.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..substrate.improver import ImproverIdentityError
from ..substrate.sandbox import SandboxTeardownError
from .clitools import (
    CliReply,
    classify_failure,
    claude_argv,
    claude_env,
    claude_system_args,
    codex_argv,
    codex_system_block,
    parse_claude_result,
    parse_codex_result,
    parse_json_object,
    retry_after_seconds,
    strip_nested_env,
)
from .spec import ChannelSpec

MAX_CONCURRENCY = int(config.PROPOSER_CONCURRENCY)
_SEM = threading.BoundedSemaphore(MAX_CONCURRENCY) if MAX_CONCURRENCY > 0 else None


_DROP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
)


def _obj(d: dict, key: str) -> dict:
    """`d[key]` when it is a dict, else {}."""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


class ProposerError(RuntimeError):
    """A call did not produce a usable reply. Never swallowed into an empty string."""

    def __init__(self, message: str, *, attempts: int = 0, cost_usd: float = 0.0):
        super().__init__(message)
        self.attempts = attempts
        self.cost_usd = cost_usd


class UsageLimitError(RuntimeError):
    """A plan usage limit with HARNESS2_STOP_ON_LIMIT=1: stop the sweep, resume it later.

    Deliberately NOT a ProposerError: a stage must not fall back on it.
    """

    def __init__(self, message: str, *, reset_hint: str = ""):
        super().__init__(message)
        self.reset_hint = reset_hint


@dataclass
class Reply:
    """One session: what came back, what it cost, and where the audit trail is."""

    label: str
    text: str
    ok: bool
    cost_usd: float = 0.0
    wall_s: float = 0.0
    attempts: int = 0
    cached: bool = False
    channel: str = ""
    error: str = ""
    audit: Path | None = None
    workspace: Path | None = None
    usage: dict = field(default_factory=dict)
    attempt_audits: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class Proposer:
    """Runs agentic sessions on one channel, with an audit trail and a cache."""

    spec: ChannelSpec
    audit_dir: Path | None = None
    attempts: int = 2
    project: str = field(default_factory=lambda: config.VERTEX_PROJECT)
    region: str = field(default_factory=lambda: config.VERTEX_REGION)
    stopped: bool = False
    _calls: list[Reply] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.spec.model.strip():
            raise ValueError("Set HARNESS2_IMPROVER_MODEL before starting model sessions")

    def env(self) -> dict[str, str]:
        """The session environment: a fresh CLI (no nested-session markers), Vertex on request."""
        e = strip_nested_env(os.environ)
        if self.spec.kind == "claude_cli":
            e = claude_env(os.environ, isolation=config.CLAUDE_ISOLATION)
            if config.CLAUDE_USE_VERTEX:
                e = {k: v for k, v in e.items() if k not in _DROP_ENV}
                e.update(
                    {
                        "CLAUDE_CODE_USE_VERTEX": "1",
                        "ANTHROPIC_VERTEX_PROJECT_ID": self.project,
                        "CLOUD_ML_REGION": self.region,
                        "ANTHROPIC_MODEL": self.spec.model,
                    }
                )
        elif self.spec.kind == "opencode":
            e = {k: v for k, v in e.items() if k not in _DROP_ENV}
            e.update(
                {
                    "GOOGLE_VERTEX_PROJECT": self.project,
                    "GOOGLE_VERTEX_LOCATION": self.region,
                    "VERTEXAI_LOCATION": self.region,
                }
            )
        return e

    def _wait_limit(self, reply: CliReply, waited: int) -> int:
        """Sleep out a plan limit (5 min doubling; 30 s doubling for a rate limit) and return
        the total waited so far; with HARNESS2_STOP_ON_LIMIT the sweep stops instead."""
        kind = classify_failure(reply.error) or "usage_limit"
        hint = retry_after_seconds(reply.error)
        if config.STOP_ON_LIMIT:
            self.stopped = True
            raise UsageLimitError(
                f"{self.spec.kind}/{self.spec.model}: {reply.error}",
                reset_hint=f"retry after {hint}s" if hint else "",
            )
        step = 300 if kind == "usage_limit" else 30
        while step <= waited:
            step *= 2
        step = min(step, max(0, config.LIMIT_MAX_WAIT - waited))
        if hint:
            step = min(step, hint)
        print(
            f"  [{self.spec.kind}] {kind} reported; waiting {step}s "
            f"({waited + step}/{config.LIMIT_MAX_WAIT}s used): {reply.error[:160]}",
            flush=True,
        )
        time.sleep(step)
        return waited + step

    def call(
        self,
        mission: str,
        context_dir: Path | str | None,
        label: str,
        *,
        system: str = "",
        timeout: int | None = None,
        max_attempts: int | None = None,
        harvest: str | None = None,
    ) -> Reply:
        """Run one session. Raises ProposerError if no usable reply after `attempts`.

        `harvest` names a subpath of the session's working directory to copy out before that
        directory is destroyed -- e.g. "context/harness", which is how an edited tree comes back.
        """

        wall = int(timeout or self.spec.timeout)
        isolation = None
        snapshot = None
        if self.spec.sandbox == "mojo-claude":
            from ..substrate.improver import runtime_identity
            from ..substrate.improver_files import read_tree

            if not self.audit_dir:
                raise ValueError("isolated improver requires a durable audit_dir")
            # Hash and copy the same checked bytes, never re-read a link or a
            # changed context after the cache key has been decided.
            snapshot = read_tree(Path(context_dir)) if context_dir else {}
            isolation = {
                "runtime": runtime_identity(),
                "channel": self.spec.__dict__,
                "timeout": wall,
                "harvest": harvest,
            }
        cache_key = _call_fingerprint(
            label, mission, system, context_dir, isolation=isolation, snapshot=snapshot
        )
        cached = self._cached(
            label, cache_key, require_workspace=bool(harvest), isolation=isolation
        )
        if cached is not None:
            self._calls.append(cached)
            return cached

        tries = max(1, int(self.attempts if max_attempts is None else max_attempts))
        channel = {
            "claude_cli": self._claude_cli,
            "codex_cli": self._codex_cli,
            "opencode": self._opencode,
        }[self.spec.kind]
        if isolation is not None:

            def channel(cwd, mission, system, wall):
                return self._claude_cli(
                    cwd, mission, system, wall, sandbox_identity=isolation["runtime"]
                )

        last = ""
        spent_usd = 0.0
        waited = 0
        attempt = 0
        last_usage = {}
        attempt_audits = []
        t0 = time.time()
        while attempt < tries:
            attempt += 1
            if attempt > 1:
                time.sleep(10 * (attempt - 1))
            audit = None
            if isolation is not None:
                from ..substrate.improver_audit import AttemptAudit

                audit = AttemptAudit(
                    self.audit_dir,
                    label=label,
                    cache_key=cache_key,
                    attempt=attempt,
                    isolation=isolation,
                    mission=mission,
                    system=system,
                )
            tmp = audit.work if audit else Path(tempfile.mkdtemp(prefix="harness2-prop-"))
            keep = audit is not None
            transcript = None
            outcome = "failed"
            r = None
            try:
                if snapshot is not None:
                    from ..substrate.improver_files import write_tree

                    write_tree(snapshot, tmp / "context")
                elif context_dir and Path(context_dir).is_dir():
                    shutil.copytree(context_dir, tmp / "context", dirs_exist_ok=True)
                with _SEM if _SEM is not None else contextlib.nullcontext():
                    reply = channel(tmp, mission, system, wall)
                transcript = getattr(reply, "transcript", None)
                spent_usd += reply.cost_usd
                last_usage = dict(reply.usage)
                if isolation is not None and runtime_identity() != isolation["runtime"]:
                    raise ImproverIdentityError("improver runtime changed; output refused")
                if not reply.ok:
                    last = reply.error or "empty reply"
                    if reply.limited and (config.STOP_ON_LIMIT or waited < config.LIMIT_MAX_WAIT):
                        waited = self._wait_limit(reply, waited)
                        attempt -= 1  # a waited-out limit does not consume an attempt
                    continue
                r = Reply(
                    label=label,
                    text=reply.text,
                    ok=True,
                    cost_usd=spent_usd,
                    wall_s=round(time.time() - t0, 1),
                    attempts=attempt,
                    channel=self.spec.kind,
                    usage=dict(reply.usage),
                )
                if harvest:
                    r.workspace = self._harvest(tmp, harvest, label, cache_key)
                    if r.workspace is None:
                        _drop_workspace(self._paths(label, cache_key))
                outcome = "accepted"
            except UsageLimitError:
                outcome = "usage_limit_stopped"
                raise
            except SandboxTeardownError:
                outcome = "teardown_unconfirmed"
                last = "improver teardown unconfirmed"
                keep = True
                raise SandboxTeardownError(
                    f"improver teardown unconfirmed; workspace retained at {tmp}"
                ) from None
            except ImproverIdentityError as exc:
                outcome = "identity_refused"
                last = str(exc)
                break
            except subprocess.TimeoutExpired:
                last = f"timeout after {wall}s"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            finally:
                if audit is not None:
                    # The raw work tree is retained in place, never followed or
                    # scanned to make this record. In particular, unknown
                    # teardown cannot turn into a harvest or another attempt.
                    attempt_audits.append(
                        audit.finish(
                            outcome=outcome,
                            error="" if outcome == "accepted" else last,
                            transcript=transcript,
                        )
                    )
                if not keep:
                    shutil.rmtree(tmp, ignore_errors=True)
            if r is not None and outcome == "accepted":
                r.attempt_audits = list(attempt_audits)
                self._write_audit(r, mission, system, cache_key, isolation=isolation)
                self._calls.append(r)
                return r

        r = Reply(
            label=label,
            text="",
            ok=False,
            wall_s=round(time.time() - t0, 1),
            attempts=attempt,
            channel=self.spec.kind,
            error=last,
            cost_usd=spent_usd,
            usage=last_usage,
            attempt_audits=attempt_audits,
        )
        self._calls.append(r)
        self._write_audit(r, mission, system, cache_key, isolation=isolation)
        raise ProposerError(
            f"[{label}] {self.spec.kind}/{self.spec.model}: {last}",
            attempts=attempt,
            cost_usd=spent_usd,
        )

    def _claude_cli(
        self,
        cwd: Path,
        mission: str,
        system: str,
        wall: int,
        *,
        sandbox_identity: dict | None = None,
    ) -> CliReply:
        if self.spec.sandbox == "mojo-claude":
            from ..substrate.improver import run_isolated

            return run_isolated(
                cwd, self.spec, mission, system, wall, expected_identity=sandbox_identity
            )
        sys_arg, stdin = claude_system_args(system, mission)
        cmd = claude_argv(
            model=self.spec.model,
            effort=self.spec.effort,
            system=sys_arg,
            skip_permissions=True,
            isolation=config.CLAUDE_ISOLATION,
        )
        p = subprocess.run(
            cmd,
            input=stdin,
            cwd=cwd,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=wall,
        )
        return parse_claude_result(p.stdout, p.returncode, p.stderr)

    def _codex_cli(self, cwd: Path, mission: str, system: str, wall: int) -> CliReply:
        last = cwd / ".harness2_last_message.txt"
        cmd = codex_argv(
            model=self.spec.model,
            effort=self.spec.effort,
            cwd=cwd,
            last_message=last,
            sandbox="full",
        )
        p = subprocess.run(
            cmd,
            input=codex_system_block(system, mission) if system else mission,
            cwd=cwd,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=wall,
        )
        return parse_codex_result(last, p.stdout, p.returncode, p.stderr)

    def _opencode(self, cwd: Path, mission: str, system: str, wall: int) -> CliReply:
        (cwd / "MISSION.md").write_text(
            (f"{system}\n\n---\n\n" if system else "") + mission, encoding="utf-8"
        )
        out = cwd / "output"
        out.mkdir(exist_ok=True)

        (cwd / "opencode.json").write_text(
            json.dumps({"agent": {"build": {"steps": self.spec.steps}}}), encoding="utf-8"
        )
        prompt = (
            "Read MISSION.md first. Do what it asks. Reply with the requested output as "
            "your final message AND save it verbatim to output/answer.md."
        )
        oc = _opencode_dir()

        env = self.env()
        env["OPENCODE_PERMISSION"] = json.dumps(
            {"external_directory": {"*": "deny", str(cwd): "allow", f"{cwd}/**": "allow"}}
        )
        env["XDG_DATA_HOME"] = str(cwd / ".ocdata")
        env["XDG_STATE_HOME"] = str(cwd / ".ocstate")

        cmd = [
            str(config.BUN),
            "run",
            "--cwd",
            str(oc / "packages" / "opencode"),
            "--conditions=browser",
            "src/index.ts",
            "run",
            prompt,
            "--dir",
            str(cwd),
            "--model",
            self.spec.model,
            "--format",
            "json",
        ]
        if self.spec.effort:
            cmd += ["--variant", self.spec.effort]
        p = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=wall,
        )
        cost = _opencode_cost(p.stdout)
        ans = out / "answer.md"
        if ans.is_file() and ans.read_text(errors="replace").strip():
            return CliReply(text=ans.read_text(errors="replace"), cost_usd=cost)
        text, err = _opencode_stdout(p.stdout)
        if text.strip():
            return CliReply(text=text, cost_usd=cost)
        tail = _strip_oc_banner(p.stderr or "").strip()[-300:]
        return CliReply(cost_usd=cost, error=f"rc={p.returncode}: {err or tail or 'empty reply'}")

    def _harvest(self, tmp: Path, sub: str, label: str, cache_key: str = "") -> Path | None:
        """Copy `tmp/<sub>` somewhere durable before the session directory is removed."""
        from ..substrate.improver_files import collect

        dest = Path(self.audit_dir or tempfile.mkdtemp(prefix="harness2-harvest-"))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        if cache_key:
            safe = f"{safe}.{cache_key}"
        dest = dest / f"{safe}.workspace"
        return collect(tmp, sub, dest)

    def _paths(self, label: str, cache_key: str = "") -> dict[str, Path] | None:
        if not self.audit_dir:
            return None
        d = Path(self.audit_dir)
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        if cache_key:
            safe = f"{safe}.{cache_key}"
        return {k: d / f"{safe}.{k}.md" for k in ("mission", "system", "response")} | {
            "meta": d / f"{safe}.meta.json",
            "workspace": d / f"{safe}.workspace",
        }

    def _cached(
        self,
        label: str,
        cache_key: str = "",
        *,
        require_workspace: bool = False,
        isolation: dict | None = None,
    ) -> Reply | None:
        p = self._paths(label, cache_key)
        if not p:
            return None
        if isolation is not None:
            from ..substrate.improver_audit import load_attempt
            from ..substrate.improver_files import plain_directory, read_file, read_tree

            plain_directory(p["response"].parent)
            for name in ("meta", "response", "workspace"):
                if p[name].is_symlink():
                    raise ValueError("linked isolated cache artifact")
            if not any(p[name].exists() for name in ("meta", "response", "workspace")):
                return None
            meta = json.loads(read_file(p["meta"]))
            raw = read_file(p["response"])
            workspace = read_tree(p["workspace"]) if p["workspace"].exists() else None
            if (
                not isinstance(meta, dict)
                or type(meta.get("ok")) is not bool
                or meta.get("sandbox") != isolation
                or meta.get("response_sha256") != hashlib.sha256(raw).hexdigest()
                or meta.get("workspace_sha256") != _tree_sha(workspace)
            ):
                raise ValueError("isolated improver cache changed or lacks its bindings")
            refs = meta.get("attempt_audits")
            if (
                not isinstance(refs, list)
                or not refs
                or len({ref["path"] for ref in refs}) != len(refs)
            ):
                raise ValueError("isolated improver cache lacks attempt audits")
            for ref in refs:
                load_attempt(self.audit_dir, ref)
            if meta["ok"] is False:
                if raw or workspace is not None:
                    raise ValueError("contradictory failed improver cache")
                return None  # Preserve the existing explicit resume/retry behavior.
            if require_workspace and workspace is None:
                return None
            txt = raw.decode("utf-8", errors="replace")
        else:
            if not p["response"].is_file() or (require_workspace and not p["workspace"].is_dir()):
                return None
            txt = p["response"].read_text(encoding="utf-8", errors="replace")
        if not txt.strip():
            return None
        return Reply(
            label=label,
            text=txt,
            ok=True,
            cost_usd=0.0,
            cached=True,
            channel=self.spec.kind,
            audit=p["response"],
            workspace=p["workspace"] if p["workspace"].is_dir() else None,
            attempt_audits=meta["attempt_audits"] if isolation is not None else [],
        )

    def _write_audit(
        self,
        r: Reply,
        mission: str,
        system: str,
        cache_key: str = "",
        *,
        isolation: dict | None = None,
    ) -> None:
        p = self._paths(r.label, cache_key)
        if not p:
            return
        p["mission"].write_text(mission, encoding="utf-8")
        if system:
            p["system"].write_text(system, encoding="utf-8")
        p["response"].write_text(r.text, encoding="utf-8")
        p["meta"].write_text(
            json.dumps(
                {
                    "label": r.label,
                    "ok": r.ok,
                    "channel": r.channel,
                    "model": self.spec.model,
                    "effort": self.spec.effort,
                    "attempts": r.attempts,
                    "wall_s": r.wall_s,
                    "cost_usd": r.cost_usd,
                    "usage": r.usage,
                    "error": r.error,
                    **(
                        {
                            "sandbox": isolation,
                            "response_sha256": hashlib.sha256(r.text.encode()).hexdigest(),
                            "workspace_sha256": _workspace_sha(r.workspace),
                            "attempt_audits": r.attempt_audits,
                        }
                        if isolation is not None
                        else {}
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        r.audit = p["response"]


def _drop_workspace(paths: dict[str, Path] | None) -> None:
    """Remove a stale harvested workspace so a later cache read cannot serve it."""
    if paths:
        shutil.rmtree(paths["workspace"], ignore_errors=True)


def _opencode_dir() -> Path:
    """The upstream OpenCode checkout to run through bun: config.OPENCODE_DIR."""
    oc = config.OPENCODE_DIR
    if not (oc / "packages" / "opencode" / "src" / "index.ts").is_file():
        raise FileNotFoundError(
            f"no OpenCode checkout at {oc} (config.OPENCODE_DIR); run "
            f"scripts/setup_benchmark.py <benchmark> to clone the pinned commit there"
        )
    return oc


_parse_json = parse_json_object


_OC_BANNER = re.compile(
    r"^(?:Performing one time database migration[^\n]*|sqlite-migration:[^\n]*"
    r"|Database migration complete\.)[ \t]*$",
    re.M,
)


def _strip_oc_banner(s: str) -> str:
    """Drop the migration banner lines from OpenCode output. No-op when they are absent."""
    return _OC_BANNER.sub("", s or "")


def _opencode_stdout(stdout: str) -> tuple[str, str]:
    """Fold `opencode run --format json` into (final assistant text, error text).

    The stream is one raw event per line and assistant prose lives at part.text of a `text`
    event. All text events are joined, so a trailing terminator event cannot swallow the
    answer; `error` events become the error string. Plain-text stdout is returned as-is.
    """
    texts: list[str] = []
    errors: list[str] = []
    saw_json = False
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        saw_json = True
        part = _obj(ev, "part")
        ptype = part.get("type") or ev.get("type")
        if ptype == "text":
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                texts.append(t)
        elif ptype == "error":
            e = ev.get("error")
            if isinstance(e, dict):
                msg = str(_obj(e, "data").get("message") or e.get("name") or "").strip()
            else:
                msg = str(e or "").strip()
            if msg:
                errors.append(msg)
    if not saw_json:
        return _strip_oc_banner(stdout).strip(), ""
    return "\n\n".join(texts), "; ".join(errors)[-300:]


def _opencode_cost(stdout: str) -> float:
    """Sum `part.cost` over the step_finish events of an `opencode run --format json` stream."""
    total = 0.0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        part = _obj(ev, "part")
        if (part.get("type") or ev.get("type")) in ("step-finish", "step_finish"):
            c = part.get("cost")
            if isinstance(c, (int, float)):
                total += float(c)
    return round(total, 6)


def _workspace_sha(path):
    from ..substrate.improver_files import read_tree

    return _tree_sha(read_tree(path) if path is not None else None)


def _tree_sha(tree):
    if tree is None:
        return None
    values = {key: hashlib.sha256(value).hexdigest() for key, value in sorted(tree.items())}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _call_fingerprint(
    label: str,
    mission: str,
    system: str,
    context_dir: Path | str | None,
    *,
    isolation=None,
    snapshot=None,
) -> str:
    """Content-address one call, including its evidence and its mode/task workspace path.

    The resolved path is part of the key: the two modes can have byte-identical k=1 evidence
    and must still be independent draws.
    """
    h = hashlib.sha256()
    for value in (label, mission, system):
        h.update(value.encode("utf-8", errors="replace"))
        h.update(b"\0")
    if isolation is not None:
        h.update(json.dumps(isolation, sort_keys=True).encode())
        h.update(b"\0")
        h.update(str(Path(context_dir).absolute() if context_dir else "").encode())
        h.update(b"\0")
        h.update(_tree_sha(snapshot).encode())
        return h.hexdigest()[:16]
    if context_dir:
        root = Path(context_dir)
        h.update(str(root.resolve()).encode("utf-8", errors="replace"))
        h.update(b"\0")
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                h.update(str(p.relative_to(root)).encode("utf-8", errors="replace"))
                h.update(b"\0")
                try:
                    with p.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                except OSError as exc:
                    h.update(f"<unreadable:{type(exc).__name__}:{exc}>".encode())
                h.update(b"\0")
    return h.hexdigest()[:16]
