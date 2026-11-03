# Modified by Light of Baldr LLC, 2026. See MODIFICATIONS.md.
"""The local OpenAI-compatible judge bridge over `claude -p` and `codex exec`."""

import base64
import importlib.util
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.cli import judge_bridge as jb
from harness2.cli.judge_bridge import Backend, Bridge, BridgeError, Request, parse_model_id
from harness2.core.clitools import CliReply

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}


def echo_backend(backend, req, *, timeout, workdir):
    return CliReply(
        text=json.dumps(
            {
                "system": req.system,
                "prompt": req.prompt,
                "images": len(req.images),
                "schema": req.schema,
                "effort": req.effort,
                "backend": backend.kind,
                "model": backend.model,
            }
        ),
        usage={"input_tokens": 1, "output_tokens": 1},
    )


class BridgeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.usage_log = Path(tmp.name) / "usage.jsonl"
        self.bridge = Bridge(
            host="127.0.0.1", port=0, key="k", concurrency=2, timeout=5,
            default_effort="low", log_dir=None, usage_log=self.usage_log,
        )
        self.port = self.bridge.start()
        self.addCleanup(self.bridge.shutdown)
        self.backend = patch.object(jb, "run_backend", side_effect=echo_backend)
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def _req(self, method, path, body=None, key="k"):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def _chat(self, model="cli/claude:opus", **extra):
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "S"},
                {"role": "user", "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]},
            ],
            "reasoning_effort": "low",
            **extra,
        }
        return self._req("POST", "/v1/chat/completions", body)

    def test_models_endpoint_lists_backends(self):
        status, body, _ = self._req("GET", "/v1/models")
        ids = [m["id"] for m in body["data"]]
        self.assertEqual(status, 200)
        self.assertIn("cli/claude:opus", ids)
        self.assertIn("cli/codex:gpt-5.6-sol", ids)

    def test_chat_completion_roundtrip(self):
        status, body, _ = self._chat()
        self.assertEqual(status, 200)
        echoed = json.loads(body["choices"][0]["message"]["content"])
        self.assertEqual(
            (echoed["system"], echoed["prompt"], echoed["effort"], body["choices"][0]["finish_reason"]),
            ("S", "A\nB", "low", "stop"),
        )
        self.assertEqual(body["usage"]["total_tokens"], 2)
        self.assertEqual(body["object"], "chat.completion")

    def test_requires_bearer_key(self):
        status, body, _ = self._req("GET", "/v1/models", key="wrong")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_rejects_stream(self):
        self.assertEqual(self._chat(stream=True)[0], 400)

    def test_unknown_model_400(self):
        self.assertEqual(self._chat(model="gpt-4o")[0], 400)
        self.assertEqual(self._req("POST", "/v1/nope", {"model": "cli/claude:opus"})[0], 404)

    def _image_chat(self, model):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "grade"},
                {"type": "image_url", "image_url": {"url": DATA_URL}},
            ]}],
        }
        return self._req("POST", "/v1/chat/completions", body)

    def test_images_refused_on_claude_422(self):
        status, body, _ = self._image_chat("cli/claude:opus")
        self.assertEqual(status, 422)
        self.assertIn("image", body["error"]["message"])

    def test_images_passed_to_codex(self):
        status, body, _ = self._image_chat("cli/codex:gpt-5.6-sol")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body["choices"][0]["message"]["content"])["images"], 1)

    def test_responses_roundtrip_with_schema(self):
        body = {"model": "cli/claude:opus", "input": "Q", "text": {"format": {"type": "json_schema", "name": "v", "schema": SCHEMA}}}
        status, out, _ = self._req("POST", "/v1/responses", body)
        self.assertEqual(status, 200)
        self.assertEqual(out["object"], "response")
        self.assertEqual(out["status"], "completed")
        part = out["output"][0]["content"][0]
        self.assertEqual(part["type"], "output_text")
        echoed = json.loads(part["text"])
        self.assertEqual((echoed["prompt"], echoed["schema"]), ("Q", SCHEMA))

    @unittest.skipUnless(importlib.util.find_spec("openai"), "openai SDK not installed")
    def test_responses_openai_sdk_output_text(self):
        import openai

        client = openai.OpenAI(base_url=f"http://127.0.0.1:{self.port}/v1", api_key="k")
        r = client.responses.create(model="cli/codex:x", input="hi")
        self.assertEqual(json.loads(r.output_text)["prompt"], "hi")
        c = client.chat.completions.create(model="cli/claude:opus", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(json.loads(c.choices[0].message.content)["prompt"], "hi")

    def test_limit_maps_to_429_with_retry_after(self):
        self.backend.stop()
        limited = CliReply(error="usage limit reached. retry after 120 seconds", limited=True)
        with patch.object(jb, "run_backend", return_value=limited):
            status, body, headers = self._chat()
        self.backend.start()
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("Retry-After"), "120")

    def test_successful_reply_is_served_even_if_flagged_limited(self):
        self.backend.stop()
        flagged = CliReply(text='{"verdict":"pass"}', limited=True)
        with patch.object(jb, "run_backend", return_value=flagged):
            status, body, _ = self._chat()
        self.backend.start()
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], '{"verdict":"pass"}')

    def test_banner_names_the_default_judge(self):
        lines = jb.banner_lines("127.0.0.1", 1234, "k")
        self.assertIn("HARNESS2_JUDGE_BASE_URL=http://127.0.0.1:1234/v1", lines)
        self.assertIn("HARNESS2_JUDGE_API_KEY=k", lines)
        model_line = next(ln for ln in lines if ln.startswith("HARNESS2_JUDGE_MODEL="))
        self.assertTrue(model_line.startswith("HARNESS2_JUDGE_MODEL=cli/claude:sonnet"))
        self.assertIn("HARNESS2_JUDGE_WORKERS=1", model_line)

    def test_timeout_maps_to_504(self):
        self.backend.stop()
        with patch.object(jb, "run_backend", side_effect=BridgeError(504, "timeout", code="timeout")):
            status, body, _ = self._chat()
        self.backend.start()
        self.assertEqual(status, 504)

    def test_backend_failure_502(self):
        self.backend.stop()
        with patch.object(jb, "run_backend", return_value=CliReply(error="rc=1: boom")):
            status, body, _ = self._chat()
        self.backend.start()
        self.assertEqual(status, 502)
        self.assertIn("boom", body["error"]["message"])

    def test_usage_log_line_written(self):
        self._chat()
        lines = self.usage_log.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["backend"], "claude")
        self.assertEqual(row["input_tokens"], 1)

    def test_concurrency_cap(self):
        self.backend.stop()

        def slow(backend, req, *, timeout, workdir):
            time.sleep(0.2)
            return echo_backend(backend, req, timeout=timeout, workdir=workdir)

        statuses = []
        with patch.object(jb, "run_backend", side_effect=slow):
            t0 = time.time()
            threads = [threading.Thread(target=lambda: statuses.append(self._chat()[0])) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            wall = time.time() - t0
        self.backend.start()
        self.assertEqual(statuses, [200] * 4)
        self.assertGreaterEqual(wall, 0.4)


class ShutdownTests(unittest.TestCase):
    def test_shutdown_kills_in_flight_cli_processes(self):
        # Stopping the bridge must not orphan a running `claude`/`codex` child: it would keep
        # burning plan quota after the server is gone.
        import subprocess as sp

        started = {}

        def slow_backend(backend, req, *, timeout, workdir):
            proc = sp.Popen(["sleep", "60"])
            started["pid"] = proc.pid
            jb.register_child(proc)
            try:
                proc.wait(timeout=timeout)
            except sp.TimeoutExpired:
                proc.kill()
            return CliReply(text="late")

        with tempfile.TemporaryDirectory() as td:
            bridge = Bridge(
                host="127.0.0.1", port=0, key="k", concurrency=1, timeout=30,
                default_effort="low", log_dir=None, usage_log=Path(td) / "u.jsonl",
            )
            port = bridge.start()
            with patch.object(jb, "run_backend", side_effect=slow_backend):
                body = json.dumps({"model": "cli/claude:opus", "messages": [{"role": "user", "content": "x"}]}).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions", data=body, method="POST",
                    headers={"content-type": "application/json", "authorization": "Bearer k"},
                )
                t = threading.Thread(target=lambda: urllib.request.urlopen(req, timeout=10), daemon=True)
                t.start()
                for _ in range(100):
                    if "pid" in started:
                        break
                    time.sleep(0.05)
                self.assertIn("pid", started)
                bridge.shutdown()
            time.sleep(0.3)
            with self.assertRaises(ProcessLookupError):
                jb.os.kill(started["pid"], 0)  # the sleep child is gone


class BackendTests(unittest.TestCase):
    def test_claude_login_drops_api_key_but_bare_keeps_it(self):
        req = Request(system="S", prompt="P", images=[], schema=None, effort="low")
        reply = SimpleNamespace(stdout='{"result":"ok","subtype":"success"}', returncode=0, stderr="")
        for isolation in ("flags", "bare"):
            with (
                self.subTest(isolation=isolation),
                tempfile.TemporaryDirectory() as td,
                patch.object(jb, "_run_cli", return_value=reply) as run,
                patch.object(jb.config, "CLAUDE_ISOLATION", isolation),
                patch.dict(jb.os.environ, {"ANTHROPIC_API_KEY": "fake-parent-key", "CLAUDECODE": "1"}, clear=True),
            ):
                result = jb.run_backend(Backend("claude", "opus"), req, timeout=5, workdir=Path(td))
                self.assertTrue(result.ok)
                child = run.call_args.kwargs["env"]
                self.assertNotIn("CLAUDECODE", child)
                if isolation == "bare":
                    self.assertEqual(child["ANTHROPIC_API_KEY"], "fake-parent-key")
                else:
                    self.assertNotIn("ANTHROPIC_API_KEY", child)
                self.assertEqual(jb.os.environ["ANTHROPIC_API_KEY"], "fake-parent-key")

    def test_parse_model_id(self):
        self.assertEqual(parse_model_id("cli/claude:opus"), Backend("claude", "opus"))
        self.assertEqual(parse_model_id("codex:gpt-5.6-sol"), Backend("codex", "gpt-5.6-sol"))
        for bad in ("claude-opus", "cli/gemini:x", "", "cli/claude:"):
            with self.assertRaises(BridgeError) as cm:
                parse_model_id(bad)
            self.assertEqual(cm.exception.status, 400)

    def test_run_backend_sends_prompt_on_stdin(self):
        calls = []

        def fake(argv, **kw):
            calls.append((list(argv), kw))
            return SimpleNamespace(stdout='{"result":"ok","subtype":"success"}', returncode=0, stderr="")

        req = Request(system="S", prompt="x" * 300_000, images=[], schema=None, effort="low")
        with tempfile.TemporaryDirectory() as td, patch.object(jb, "_run_cli", fake):
            reply = jb.run_backend(Backend("claude", "opus"), req, timeout=5, workdir=Path(td))
        argv, kw = calls[0]
        self.assertEqual(reply.text, "ok")
        self.assertEqual(len(kw["input"]), 300_000)
        self.assertTrue(all(len(a) < 10_000 for a in argv))
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "S")
        self.assertNotIn("CLAUDECODE", kw["env"])

    def test_run_backend_large_system_goes_to_stdin(self):
        calls = []

        def fake(argv, **kw):
            calls.append((list(argv), kw))
            return SimpleNamespace(stdout='{"result":"ok","subtype":"success"}', returncode=0, stderr="")

        big = "s" * 200_000
        req = Request(system=big, prompt="P", images=[], schema=None, effort="low")
        with tempfile.TemporaryDirectory() as td, patch.object(jb, "_run_cli", fake):
            jb.run_backend(Backend("claude", "opus"), req, timeout=5, workdir=Path(td))
        argv, kw = calls[0]
        self.assertTrue(all(len(a) < 10_000 for a in argv))
        self.assertTrue(kw["input"].startswith("# SYSTEM INSTRUCTIONS"))
        self.assertTrue(kw["input"].endswith("P"))

    def test_run_backend_claude_refuses_images(self):
        req = Request(system="", prompt="p", images=[("a.png", PNG_BYTES)], schema=None, effort="low")
        with tempfile.TemporaryDirectory() as td, self.assertRaises(BridgeError) as cm:
            jb.run_backend(Backend("claude", "opus"), req, timeout=5, workdir=Path(td))
        self.assertEqual(cm.exception.status, 422)

    def test_run_backend_codex_writes_schema_and_images(self):
        seen = {}

        def fake(argv, **kw):
            schema = Path(argv[argv.index("--output-schema") + 1])
            img = Path(argv[argv.index("-i") + 1])
            seen["schema"] = json.loads(schema.read_text())
            seen["png"] = img.read_bytes()[:8]
            seen["stdin"] = kw["input"]
            Path(argv[argv.index("-o") + 1]).write_text('{"verdict":"pass"}')
            return SimpleNamespace(stdout="", returncode=0, stderr="")

        req = Request(system="S", prompt="p", images=[("a.png", PNG_BYTES)], schema=SCHEMA, effort="low")
        with tempfile.TemporaryDirectory() as td, patch.object(jb, "_run_cli", fake):
            reply = jb.run_backend(Backend("codex", "gpt-5.6-sol"), req, timeout=5, workdir=Path(td))
        self.assertEqual(reply.text, '{"verdict":"pass"}')
        self.assertEqual(seen["schema"], jb.strict_schema(SCHEMA))  # codex needs the strict form
        self.assertEqual(seen["png"], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(seen["stdin"].startswith("# SYSTEM INSTRUCTIONS"))

    def test_run_backend_codex_strictifies_schema(self):
        # codex --output-schema is strict: every object needs additionalProperties:false and a
        # full required list, which upstream judge schemas do not always carry.
        loose = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
                "detail": {"type": "object", "properties": {"note": {"type": "string"}}},
                "items": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "string"}}}},
            },
            "required": ["verdict"],
        }
        seen = {}

        def fake(argv, **kw):
            seen["schema"] = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
            Path(argv[argv.index("-o") + 1]).write_text("{}")
            return SimpleNamespace(stdout="", returncode=0, stderr="")

        req = Request(system="", prompt="p", images=[], schema=loose, effort="low")
        with tempfile.TemporaryDirectory() as td, patch.object(jb, "_run_cli", fake):
            jb.run_backend(Backend("codex", "m"), req, timeout=5, workdir=Path(td))
        got = seen["schema"]
        self.assertIs(got["additionalProperties"], False)
        self.assertEqual(sorted(got["required"]), ["detail", "items", "verdict"])
        self.assertIs(got["properties"]["detail"]["additionalProperties"], False)
        self.assertEqual(got["properties"]["detail"]["required"], ["note"])
        self.assertIs(got["properties"]["items"]["items"]["additionalProperties"], False)
        self.assertEqual(jb.strict_schema({"type": "string"}), {"type": "string"})

    def test_run_backend_codex_env_is_confined(self):
        # A prompt-injected judge must not find the real home directory: HOME is the
        # throwaway workdir, while auth still resolves through the real CODEX_HOME.
        seen = {}

        def fake(argv, **kw):
            seen.update(kw)
            seen["argv"] = list(argv)
            Path(argv[argv.index("-o") + 1]).write_text("ok")
            return SimpleNamespace(stdout="", returncode=0, stderr="")

        req = Request(system="", prompt="p", images=[], schema=None, effort="low")
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(jb, "_run_cli", fake),
            patch.dict(jb.os.environ, {"HOME": "/home/someone"}, clear=False),
        ):
            jb.os.environ.pop("CODEX_HOME", None)
            jb.run_backend(Backend("codex", "m"), req, timeout=5, workdir=Path(td))
            self.assertEqual(seen["env"]["HOME"], td)
            self.assertEqual(seen["env"]["CODEX_HOME"], "/home/someone/.codex")
            self.assertEqual(seen["cwd"], Path(td))
            self.assertEqual(seen["argv"][seen["argv"].index("-s") + 1], "read-only")

    def test_run_backend_timeout_is_504(self):
        def fake(argv, **kw):
            raise jb.subprocess.TimeoutExpired(argv, 5)

        req = Request(system="", prompt="p", images=[], schema=None, effort="low")
        with tempfile.TemporaryDirectory() as td, patch.object(jb, "_run_cli", fake):
            with self.assertRaises(BridgeError) as cm:
                jb.run_backend(Backend("codex", "m"), req, timeout=5, workdir=Path(td))
        self.assertEqual(cm.exception.status, 504)


if __name__ == "__main__":
    unittest.main()
