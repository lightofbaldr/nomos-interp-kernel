#!/usr/bin/env python3
"""Offline, CPU-only prompt measurement. Not a production context guarantee.

Run with the reviewed interpreter's ``-I -B`` flags and an operator-owned manifest:
    python -I -B scripts/agent-prompt-count.py --manifest /absolute/manifest.json
The exact OpenAI request is read from stdin, never from a path in the request.

Manifest shape (all keys required; paths are operator inputs, not model inputs):
    {"schema":"glimmer.agent-prompt-count/1", "context_capacity":32768,
     "reserve_tokens":2048,
     "tokenizer":{"path":"/absolute/tokenizer-directory", "files":{
       "tokenizer.json":"<sha256>", "tokenizer_config.json":"<sha256>",
       "chat_template.jinja":"<sha256>", "config.json":"<sha256>",
       "generation_config.json":"<sha256>"}},
     "protocol":{"path":"/absolute/serve_protocol.py", "sha256":"<sha256>"},
     "versions":{"transformers":"5.5.4", "tokenizers":"0.22.2", "jinja2":"3.1.6"}}

All source assets are descriptor-pinned, no-follow, single-link regular files.
The loader sees only private copies of verified bytes, not mutable source paths.
Source identities and hashes are checked again after counting. The manifest is
an operator trust decision, NOT proof of the running kernel's protocol/capacity.
No serve.py, model, GPU/kernel code, .env, credentials, or downloads are loaded.
Exit 0 means this payload fits the manifest assertion. Exit 2 is a refusal;
CONTEXT_LIMIT still returns its count-only result with fits=false. Errors never
contain the input, rendered prompt, local paths, or exception messages.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import stat
import sys
import tempfile


SCHEMA = "glimmer.agent-prompt-count/1"
CAPACITY = 32768
RESERVE = 2048
MAX_BODY = 1024 * 1024
MAX_ASSET = 64 * 1024 * 1024
MAX_RENDERED = 8 * 1024 * 1024
ASSETS = {"tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "config.json", "generation_config.json"}
VERSION_KEYS = {"transformers", "tokenizers", "jinja2"}
ACCELERATOR_MODULES = {"torch", "torchvision", "torchaudio", "tensorflow", "jax", "jaxlib", "pycuda", "triton", "mlx", "cupy", "bitsandbytes", "accelerate"}


class Refusal(Exception):
    def __init__(self, code: str):
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "INVALID_JSON")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise Refusal("INVALID_JSON")

    try:
        result = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=invalid_constant)
        pending = [(result, 0)]
        visited = 0
        while pending:
            value, depth = pending.pop()
            visited += 1
            require(depth <= 48 and visited <= 100000, "INPUT_LIMIT")
            if isinstance(value, dict):
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                value.encode("utf-8", errors="strict")
            elif type(value) is float:
                require(math.isfinite(value), "INVALID_JSON")
        return result
    except Refusal:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal("INVALID_JSON") from None


def keys(value, allowed: set[str], required: set[str] | frozenset[str] = frozenset(), code="INVALID_REQUEST"):
    require(type(value) is dict and required <= value.keys() <= allowed, code)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def path_parts(value: str) -> list[str]:
    require(type(value) is str and len(value) <= 4096 and value.startswith("/") and
            str(Path(value)) == value and "\0" not in value, "UNSAFE_ASSET")
    parts = value.split("/")[1:]
    require(0 < len(parts) <= 64 and all(p and p not in (".", "..") for p in parts), "UNSAFE_ASSET")
    return parts


@contextlib.contextmanager
def directory(value: str):
    parts = path_parts(value)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def regular(info, limit, private=False):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and
            0 <= info.st_size <= limit and (not private or not info.st_mode & 0o077), "UNSAFE_ASSET")


def descriptor_bytes(fd, limit):
    parts = []
    used = 0
    while used <= limit:
        chunk = os.pread(fd, min(1024 * 1024, limit + 1 - used), used)
        if not chunk:
            break
        parts.append(chunk)
        used += len(chunk)
    require(used <= limit, "ASSET_LIMIT")
    return b"".join(parts)


@contextlib.contextmanager
def pinned(value: str, limit=MAX_ASSET, private=False):
    parts = path_parts(value)
    parent = "/" + "/".join(parts[:-1])
    # No expected asset is a top-level file; avoid a broad root target.
    require(parent != "/", "UNSAFE_ASSET")
    with directory(parent) as parent_fd:
        pin = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
        reader = None
        try:
            before = os.fstat(pin)
            regular(before, limit, private)
            reader = os.open(f"/proc/self/fd/{pin}", os.O_RDONLY | os.O_NONBLOCK)
            require(fingerprint(os.fstat(reader)) == fingerprint(before), "ASSET_CHANGED")
            data = descriptor_bytes(reader, limit)
            require(len(data) == before.st_size and fingerprint(os.fstat(reader)) == fingerprint(before), "ASSET_CHANGED")
            yield data
            # Verify the held object AND re-resolve the canonical source path.
            require(fingerprint(os.fstat(pin)) == fingerprint(before) and
                    sha(descriptor_bytes(reader, limit)) == sha(data), "ASSET_CHANGED")
            with directory(parent) as fresh_parent:
                fresh = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=fresh_parent)
                try:
                    require(fingerprint(os.fstat(fresh)) == fingerprint(before), "ASSET_CHANGED")
                finally:
                    os.close(fresh)
        finally:
            if reader is not None:
                os.close(reader)
            os.close(pin)


def inventory(value: str):
    with directory(value) as fd:
        before = os.fstat(fd)
        require(before.st_uid == os.getuid(), "UNSAFE_ASSET")
        names = set()
        with os.scandir(fd) as scan:
            for entry in scan:
                require(len(names) < len(ASSETS) and entry.name in ASSETS and entry.is_file(follow_symlinks=False), "UNPINNED_ASSET")
                names.add(entry.name)
        require(names == ASSETS and fingerprint(os.fstat(fd)) == fingerprint(before), "UNPINNED_ASSET")
        return fingerprint(before)


def validate_manifest(manifest):
    keys(manifest, {"schema", "context_capacity", "reserve_tokens", "tokenizer", "protocol", "versions"},
         {"schema", "context_capacity", "reserve_tokens", "tokenizer", "protocol", "versions"}, "INVALID_MANIFEST")
    require(manifest["schema"] == SCHEMA and type(manifest["context_capacity"]) is int and
            manifest["context_capacity"] == CAPACITY and type(manifest["reserve_tokens"]) is int and
            manifest["reserve_tokens"] == RESERVE, "INVALID_MANIFEST")
    keys(manifest["tokenizer"], {"path", "files"}, {"path", "files"}, "INVALID_MANIFEST")
    keys(manifest["protocol"], {"path", "sha256"}, {"path", "sha256"}, "INVALID_MANIFEST")
    keys(manifest["versions"], VERSION_KEYS, VERSION_KEYS, "INVALID_MANIFEST")
    keys(manifest["tokenizer"]["files"], ASSETS, ASSETS, "INVALID_MANIFEST")
    for digest in [manifest["protocol"]["sha256"], *manifest["tokenizer"]["files"].values()]:
        require(type(digest) is str and re.fullmatch(r"[a-f0-9]{64}", digest) is not None, "INVALID_MANIFEST")
    path_parts(manifest["tokenizer"]["path"])
    path_parts(manifest["protocol"]["path"])
    require(Path(manifest["protocol"]["path"]).name == "serve_protocol.py", "INVALID_MANIFEST")
    require(all(type(v) is str and re.fullmatch(r"[0-9A-Za-z.+-]{1,64}", v) for v in manifest["versions"].values()), "INVALID_MANIFEST")


def text(value, nullable=False):
    require(type(value) is str or (nullable and value is None), "INVALID_REQUEST")


def content(value):
    if value is None or type(value) is str:
        return
    require(type(value) is list and len(value) <= 256, "UNSUPPORTED_CONTENT")
    for part in value:
        keys(part, {"type", "text"}, {"type", "text"}, "UNSUPPORTED_CONTENT")
        require(part["type"] == "text" and type(part["text"]) is str, "UNSUPPORTED_CONTENT")


def validate_request(body):
    keys(body, {"messages", "model", "tools", "tool_choice", "max_tokens", "temperature", "top_p", "top_k",
                "repetition_penalty", "stream", "stream_options", "enable_thinking", "nomos"}, {"messages", "max_tokens"})
    require(type(body["max_tokens"]) is int and 1 <= body["max_tokens"] <= 8192, "INVALID_OUTPUT_LIMIT")
    require(type(body["messages"]) is list and 0 < len(body["messages"]) <= 512, "INVALID_REQUEST")
    require(sum(m.get("role") == "system" for m in body["messages"] if type(m) is dict) <= 8, "INPUT_LIMIT")
    for message in body["messages"]:
        keys(message, {"role", "content", "name", "tool_call_id", "tool_calls", "reasoning_content", "recipient", "end_turn"}, {"role"})
        require(message["role"] in ("system", "user", "assistant", "tool"), "UNSUPPORTED_CONTENT")
        require("content" in message or (message["role"] == "assistant" and message.get("tool_calls")), "INVALID_REQUEST")
        content(message.get("content"))
        for field in ("name", "tool_call_id", "reasoning_content", "recipient"):
            if field in message:
                text(message[field], nullable=True)
        if "end_turn" in message:
            require(type(message["end_turn"]) is bool or message["end_turn"] is None, "INVALID_REQUEST")
        if message.get("tool_calls") is not None:
            calls = message["tool_calls"]
            require(message["role"] == "assistant" and type(calls) is list and len(calls) <= 64, "INVALID_REQUEST")
            for call in calls:
                keys(call, {"id", "type", "function"}, {"type", "function"})
                require(call["type"] == "function", "UNSUPPORTED_CONTENT")
                if "id" in call:
                    text(call["id"])
                keys(call["function"], {"name", "arguments"}, {"name", "arguments"})
                text(call["function"]["name"])
                require(type(call["function"]["arguments"]) in (str, dict) or call["function"]["arguments"] is None, "INVALID_REQUEST")
    tools = body.get("tools")
    require(tools is None or (type(tools) is list and len(tools) <= 64), "INVALID_REQUEST")
    for tool in tools or []:
        keys(tool, {"type", "function"}, {"type", "function"})
        require(tool["type"] == "function", "UNSUPPORTED_CONTENT")
        keys(tool["function"], {"name", "description", "parameters", "strict"}, {"name", "description", "parameters"})
        text(tool["function"]["name"])
        text(tool["function"]["description"])
        require(type(tool["function"]["parameters"]) is dict, "INVALID_REQUEST")
        if "strict" in tool["function"]:
            require(type(tool["function"]["strict"]) is bool, "INVALID_REQUEST")
    choice = body.get("tool_choice")
    if type(choice) is dict:
        keys(choice, {"type", "function"}, {"type", "function"})
        require(choice["type"] == "function", "INVALID_REQUEST")
        keys(choice["function"], {"name"}, {"name"})
        text(choice["function"]["name"])
    else:
        require(choice is None or choice in ("auto", "none", "required"), "INVALID_REQUEST")
    for field in ("stream", "enable_thinking"):
        if field in body:
            require(type(body[field]) is bool, "INVALID_REQUEST")
    if "stream_options" in body:
        keys(body["stream_options"], {"include_usage"})
        require(type(body["stream_options"].get("include_usage", True)) is bool, "INVALID_REQUEST")
    if "model" in body:
        text(body["model"], nullable=True)
    for field in ("temperature", "top_p", "top_k", "repetition_penalty"):
        if field in body:
            value = body[field]
            require((field == "repetition_penalty" and value is None) or
                    (type(value) in (int, float) and math.isfinite(value)), "INVALID_REQUEST")
    # `nomos` is opaque measurement/readout metadata, never template kwargs.


def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
        raise Refusal("OFFLINE_ONLY")
    if event == "import" and args and str(args[0]).split(".")[0] in ACCELERATOR_MODULES:
        raise Refusal("CPU_ONLY")


def tokenizer_only_imports():
    # Transformers 5 no longer honors USE_TORCH=0. Hide optional accelerator
    # discovery in this short-lived counter; actual imports remain hard-refused
    # by audit(). This does not change the tokenizer or the canonical recipe.
    find_spec = importlib.util.find_spec

    def cpu_spec(name, package=None):
        return None if name.split(".")[0] in ACCELERATOR_MODULES else find_spec(name, package)

    require(not any(name.split(".")[0] in ACCELERATOR_MODULES for name in sys.modules), "CPU_ONLY")
    importlib.util.find_spec = cpu_spec


class PinnedProtocolLoader(importlib.machinery.SourceFileLoader):
    """Compile only the hashed bytes, never a re-opened source/cache pathname."""
    def __init__(self, name, filename, source):
        super().__init__(name, filename)
        self.source = source

    def get_code(self, fullname):
        return self.source_to_code(self.source, self.path)


@contextlib.contextmanager
def quiet():
    """Suppress even native-library diagnostics that could quote request data."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    sink = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(sink, 1)
        os.dup2(sink, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in [*saved, sink]:
            os.close(fd)


def counter(manifest_path: str, raw: bytes):
    require(len(raw) <= MAX_BODY, "INPUT_LIMIT")
    body = load_json(raw)
    validate_request(body)
    result = None
    with contextlib.ExitStack() as stack:
        manifest_data = stack.enter_context(pinned(manifest_path, 65536, private=True))
        manifest = load_json(manifest_data)
        validate_manifest(manifest)
        for package, expected in manifest["versions"].items():
            require(importlib.metadata.version(package) == expected, "VERSION_MISMATCH")
        source = manifest["tokenizer"]["path"]
        before = inventory(source)
        assets = {}
        for name in sorted(ASSETS):
            data = stack.enter_context(pinned(str(Path(source) / name)))
            require(sha(data) == manifest["tokenizer"]["files"][name], "PIN_MISMATCH")
            assets[name] = data
        protocol_bytes = stack.enter_context(pinned(manifest["protocol"]["path"], 1024 * 1024))
        require(sha(protocol_bytes) == manifest["protocol"]["sha256"], "PIN_MISMATCH")
        for name in ("config.json", "tokenizer_config.json"):
            config = load_json(assets[name])
            require(type(config) is dict and not config.get("auto_map") and not config.get("custom_pipelines"), "REMOTE_CODE_REFUSED")
            for key, value in config.items():
                if key.endswith("_file") and value is not None:
                    require(type(value) is str and value in ASSETS, "UNPINNED_ASSET")
            if name == "tokenizer_config.json":
                require(config.get("tokenizer_class") == "TokenizersBackend", "REMOTE_CODE_REFUSED")
        with tempfile.TemporaryDirectory(prefix="glimmer-prompt-counter-", dir="/tmp") as temporary:
            root = Path(temporary)
            tokenizer_path = root / "tokenizer"
            tokenizer_path.mkdir(mode=0o700)
            for name, data in assets.items():
                target = tokenizer_path / name
                with target.open("xb") as handle:
                    os.chmod(target, 0o600)
                    handle.write(data)
            protocol_path = root / "serve_protocol.py"
            with protocol_path.open("xb") as handle:
                os.chmod(protocol_path, 0o600)
                handle.write(protocol_bytes)
            os.environ.update(HF_HOME=str(root / "cache"), HF_HUB_CACHE=str(root / "cache"),
                              TRANSFORMERS_CACHE=str(root / "cache"))
            # Copy hashes are checked immediately before and after loader use.
            with contextlib.ExitStack() as copies:
                for name in sorted(ASSETS):
                    copied = copies.enter_context(pinned(str(tokenizer_path / name)))
                    require(sha(copied) == manifest["tokenizer"]["files"][name], "ASSET_CHANGED")
                copied_protocol = copies.enter_context(pinned(str(protocol_path), 1024 * 1024))
                require(sha(copied_protocol) == manifest["protocol"]["sha256"], "ASSET_CHANGED")
                from transformers import AutoTokenizer
                tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path), local_files_only=True, trust_remote_code=False)
                require(type(tokenizer.chat_template) is str and
                        tokenizer.chat_template == assets["chat_template.jinja"].decode("utf-8"), "TEMPLATE_MISMATCH")
                module_name = "_glimmer_prompt_counter_protocol"
                loader = PinnedProtocolLoader(module_name, str(protocol_path), copied_protocol)
                spec = importlib.util.spec_from_file_location(module_name, protocol_path, loader=loader)
                require(spec is not None and spec.loader is not None, "PROTOCOL_MISMATCH")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                protocol = module.AtemServeProtocol(tokenizer, "measurement-only")
                require(protocol.kind == "atem" and protocol.uses_gemma_grammar is False, "PROTOCOL_MISMATCH")
                use_tools = bool(body.get("tools")) and body.get("tool_choice") != "none"
                force_tool = use_tools and (body.get("tool_choice") == "required" or type(body.get("tool_choice")) is dict)
                thinking = protocol.resolve_thinking(body.get("enable_thinking", True), force_tool)
                require(thinking is True, "PROTOCOL_MISMATCH")
                # Exact text branch of pyhost/serve.py:_build_inputs. No serve
                # import, tensor conversion, model initialization or generation.
                messages = protocol.normalize(body["messages"])
                rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                         enable_thinking=thinking, tools=body.get("tools") if use_tools else None)
                rendered = protocol.finish_prompt(rendered, thinking)
                encoded = rendered.encode("utf-8")
                require(len(encoded) <= MAX_RENDERED, "RENDER_LIMIT")
                ids = tokenizer(rendered, add_special_tokens=False).input_ids
                count = len(ids)
                requested = body["max_tokens"]
                result = {"prompt_tokens": count, "requested_output": requested, "reserve_tokens": RESERVE,
                          "capacity": CAPACITY, "fits": count + requested + RESERVE <= CAPACITY,
                          "manifest_sha256": sha(canonical(manifest)),
                          "tokenizer_sha256": sha(canonical(manifest["tokenizer"]["files"])),
                          "protocol_sha256": sha(protocol_bytes), "payload_sha256": sha(raw),
                          "rendered_sha256": sha(encoded)}
        require(inventory(source) == before, "ASSET_CHANGED")
    # Exiting every pin above performs after-use hash and path identity checks.
    return result


def main() -> int:
    try:
        require(sys.platform == "linux" and sys.flags.isolated and sys.dont_write_bytecode, "ISOLATED_RUNTIME_REQUIRED")
        require(len(sys.argv) == 3 and sys.argv[1] == "--manifest", "INVALID_ARGUMENTS")
        # Remove inherited credentials before importing the trusted tokenizer
        # libraries. No home directory or environment file is consulted.
        os.environ.clear()
        os.environ.update(PATH="/usr/bin:/bin", LANG="C.UTF-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          HF_DATASETS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
                          USE_TORCH="0", USE_TF="0", USE_FLAX="0", CUDA_VISIBLE_DEVICES="", NVIDIA_VISIBLE_DEVICES="none",
                          OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", TRANSFORMERS_NO_ADVISORY_WARNINGS="1")
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 35))
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        def timed_out(_signal, _frame):
            raise Refusal("TIME_LIMIT")

        signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(45)
        sys.addaudithook(audit)
        tokenizer_only_imports()
        raw = sys.stdin.buffer.read(MAX_BODY + 1)
        with quiet():
            result = counter(sys.argv[2], raw)
        signal.alarm(0)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        if not result["fits"]:
            print('{"error":"CONTEXT_LIMIT"}', file=sys.stderr)
            return 2
        return 0
    except Refusal as error:
        print(json.dumps({"error": error.code}, separators=(",", ":")), file=sys.stderr)
    except Exception:
        print('{"error":"COUNT_UNAVAILABLE"}', file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
