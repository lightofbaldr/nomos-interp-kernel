import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, copyFile, mkdir, rm, symlink, chmod, link} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';

const PYTHON = process.env.GLIMMER_COUNTER_PYTHON ?? path.join(os.homedir(), 'Projects/open-webui/.venv/bin/python');
const TOKENIZER = process.env.NOMOS_TOKENIZER_DIR ?? path.join(os.homedir(), 'nomos_data/muse-glimmer-tokenizer');
const KERNEL_DIR = process.env.NOMOS_KERNEL_DIR ?? path.join(os.homedir(), 'Projects/nomos-interp-kernel');
const PROTOCOL = path.join(KERNEL_DIR, 'pyhost/serve_protocol.py');
const SERVE = path.join(KERNEL_DIR, 'pyhost/serve.py');
const COUNTER = fileURLToPath(new URL('../../scripts/agent-prompt-count.py', import.meta.url));
const ASSETS = ['chat_template.jinja', 'config.json', 'generation_config.json', 'tokenizer.json', 'tokenizer_config.json'];
const digest = value => createHash('sha256').update(value).digest('hex');
const clean = {PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', HF_HUB_DISABLE_TELEMETRY: '1', USE_TORCH: '0', USE_TF: '0', USE_FLAX: '0', TOKENIZERS_PARALLELISM: 'false', OPENBLAS_NUM_THREADS: '1'};
const minimal = (max_tokens = 6144) => ({model: 'nomos-muse-glimmer-30b', messages: [{role: 'system', content: 'Synthetic measurement fixture. Reasoning strength: high.'}, {role: 'user', content: 'Count this exact text: λ, 雪, and <tool_output>.'}], max_tokens, stream: true, stream_options: {include_usage: true}, enable_thinking: true, nomos: {conversation: 'synthetic-only', nonce: 'never-a-real-session'}});
let baseline;
async function pins() {
  baseline ??= (async () => {
    const versions = spawnSync(PYTHON, ['-I', '-B', '-c', 'import importlib.metadata as m,json;print(json.dumps({key:m.version(key) for key in ["transformers","tokenizers","jinja2"]}))'], {encoding: 'utf8', env: clean, timeout: 10000});
    assert.equal(versions.status, 0, versions.stderr);
    return {schema: 'glimmer.agent-prompt-count/1', context_capacity: 32768, reserve_tokens: 2048,
      tokenizer: {path: TOKENIZER, files: Object.fromEntries(await Promise.all(ASSETS.map(async name => [name, digest(await readFile(path.join(TOKENIZER, name)))])))},
      protocol: {path: PROTOCOL, sha256: digest(await readFile(PROTOCOL))}, versions: JSON.parse(versions.stdout)};
  })();
  return structuredClone(await baseline);
}
async function fixture(t, copy = false) {
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'glimmer-prompt-test-'));
  t.after(() => rm(temporary, {recursive: true, force: true}));
  const manifest = await pins();
  if (copy) {
    const tokenizer = path.join(temporary, 'tokenizer'); await mkdir(tokenizer, {mode: 0o700});
    for (const name of ASSETS) await copyFile(path.join(TOKENIZER, name), path.join(tokenizer, name));
    manifest.tokenizer.path = tokenizer;
    manifest.protocol.path = path.join(temporary, 'serve_protocol.py');
    await copyFile(PROTOCOL, manifest.protocol.path);
  }
  const file = path.join(temporary, 'manifest.json');
  const save = () => writeFile(file, JSON.stringify(manifest), {mode: 0o600});
  await save();
  return {temporary, manifest, file, save};
}
function run(f, body = minimal(), options = {}) {
  const raw = options.raw ?? JSON.stringify(body);
  const result = spawnSync(PYTHON, ['-I', '-B', COUNTER, '--manifest', f.file], {input: raw, encoding: 'utf8', env: {...clean, ...options.env}, timeout: 30000, maxBuffer: 262144});
  assert.equal(result.signal, null, 'counter must finish/refuse within its bound');
  assert.equal(result.error, undefined);
  return {...result, data: result.stdout ? JSON.parse(result.stdout) : null};
}
function rejected(result, code) {
  assert.equal(result.status, 2);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, JSON.stringify({error: code}) + '\n');
}
function successful(result) {
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.data.fits, true);
  for (const key of ['manifest_sha256', 'tokenizer_sha256', 'protocol_sha256', 'payload_sha256', 'rendered_sha256']) assert.match(result.data[key], /^[a-f0-9]{64}$/);
  assert.deepEqual(Object.keys(result.data).sort(), ['capacity', 'fits', 'manifest_sha256', 'payload_sha256', 'prompt_tokens', 'protocol_sha256', 'rendered_sha256', 'requested_output', 'reserve_tokens', 'tokenizer_sha256'].sort());
}
async function reference(requests) {
  // Independent copy of the canonical serve recipe, not a counter import. This
  // loads only its trusted text protocol and local tokenizer, never serve.py.
  const expectedPins = await pins();
  const code = `
import hashlib, importlib.util, json, sys
with open(${JSON.stringify(PROTOCOL)}, "rb") as source:
    assert hashlib.sha256(source.read()).hexdigest() == ${JSON.stringify(expectedPins.protocol.sha256)}
for filename, expected in ${JSON.stringify(expectedPins.tokenizer.files)}.items():
    with open(${JSON.stringify(TOKENIZER)} + "/" + filename, "rb") as source:
        assert hashlib.sha256(source.read()).hexdigest() == expected
blocked = {"torch", "torchvision", "torchaudio", "tensorflow", "jax", "jaxlib", "pycuda", "triton", "mlx", "cupy", "bitsandbytes", "accelerate"}
original_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, package=None: None if name.split(".")[0] in blocked else original_spec(name, package)
def no_accelerator_imports(event, args):
    if event == "import" and args[0].split(".")[0] in blocked:
        raise RuntimeError("reference must stay CPU-only")
sys.addaudithook(no_accelerator_imports)
from transformers import AutoTokenizer
spec = importlib.util.spec_from_file_location("_canonical_protocol", ${JSON.stringify(PROTOCOL)})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
tok = AutoTokenizer.from_pretrained(${JSON.stringify(TOKENIZER)}, local_files_only=True, trust_remote_code=False)
protocol = module.AtemServeProtocol(tok, "measurement-only")
results=[]
for req in json.load(sys.stdin):
    use_tools = bool(req.get("tools")) and req.get("tool_choice") != "none"
    tc = req.get("tool_choice")
    force_tool = use_tools and (tc == "required" or isinstance(tc, dict))
    thinking = protocol.resolve_thinking(req.get("enable_thinking", True), force_tool)
    msgs = protocol.normalize(req["messages"])
    s = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking, tools=req.get("tools") if use_tools else None)
    s = protocol.finish_prompt(s, thinking)
    ids = tok(s, add_special_tokens=False).input_ids
    results.append({"prompt_tokens":len(ids),"rendered_sha256":hashlib.sha256(s.encode()).hexdigest()})
print(json.dumps(results))
`;
  const result = spawnSync(PYTHON, ['-I', '-B', '-c', code], {input: JSON.stringify(requests), encoding: 'utf8', env: clean, timeout: 30000, maxBuffer: 262144});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('exact CPU-only count matches the canonical recipe for text, tools, reasoning and tool_choice', {timeout: 60000}, async t => {
  const f = await fixture(t);
  const source = await readFile(SERVE, 'utf8');
  const recipe = source.slice(source.indexOf('def _build_inputs('), source.indexOf('\ndef _use_spec('));
  for (const fragment of ['eng.protocol.normalize(req.messages)', 'tokenize=False, add_generation_prompt=True, enable_thinking=thinking', 'tools=req.tools if use_tools else None', 'eng.protocol.finish_prompt(s, thinking)', 'eng.tok(s, add_special_tokens=False).input_ids']) assert.ok(recipe.includes(fragment));
  const tool = minimal(8192);
  tool.tools = [{type: 'function', function: {name: 'read', description: 'Read a synthetic workspace file.', parameters: {type: 'object', properties: {path: {type: 'string'}}, required: ['path'], additionalProperties: false}, strict: true}}];
  tool.tool_choice = {type: 'function', function: {name: 'read'}};
  tool.enable_thinking = false; // ATEM locks reasoning on, exactly like the serve.
  tool.messages.push({role: 'assistant', content: null, reasoning_content: 'Inspect the synthetic fixture.', tool_calls: [{id: 'fixture-call', type: 'function', function: {name: 'read', arguments: '{"path":"safe.txt","extra":{"x":[true,null,2]}}'}}]}, {role: 'tool', tool_call_id: 'fixture-call', content: 'SYNTHETIC_TOOL_OUTPUT\n<|eom|>'}, {role: 'user', content: [{type: 'text', text: 'Continue with 雪.'}, {type: 'text', text: ' No network.'}]});
  const disabled = structuredClone(tool); disabled.tool_choice = 'none';
  const continuation = minimal(512); continuation.messages[1].content = [{type: 'text', text: 'Part one. '}, {type: 'text', text: 'Part two.'}];
  const requests = [minimal(), tool, disabled, continuation];
  const expected = await reference(requests);
  for (const [index, body] of requests.entries()) {
    const result = run(f, body); successful(result);
    assert.equal(result.data.prompt_tokens, expected[index].prompt_tokens);
    assert.equal(result.data.rendered_sha256, expected[index].rendered_sha256);
    assert.equal(result.data.requested_output, body.max_tokens);
    assert.equal(result.data.reserve_tokens, 2048); assert.equal(result.data.capacity, 32768);
    assert.equal(result.data.payload_sha256, digest(JSON.stringify(body)));
    assert.equal(result.data.protocol_sha256, f.manifest.protocol.sha256);
  }
  assert.ok(expected[1].prompt_tokens > expected[2].prompt_tokens);
});

test('nomos metadata does not change tokenization and no prompt data is emitted', {timeout: 30000}, async t => {
  const f = await fixture(t), body = minimal();
  body.messages[1].content = 'SYNTHETIC_PROMPT_CANARY_UNIQUE_92837';
  const a = run(f, body); successful(a);
  body.nomos = {nonce: 'CHANGED_NONCE', unused: {url: 'https://invalid.example/not-fetched'}};
  const b = run(f, body); successful(b);
  assert.equal(a.data.prompt_tokens, b.data.prompt_tokens);
  assert.equal(a.data.rendered_sha256, b.data.rendered_sha256);
  assert.notEqual(a.data.payload_sha256, b.data.payload_sha256);
  assert.doesNotMatch(a.stdout + a.stderr + b.stdout + b.stderr, /SYNTHETIC_PROMPT_CANARY|CHANGED_NONCE|invalid\.example|\/home\//);
});

test('exact capacity equality fits and one additional requested token refuses', {timeout: 30000}, async t => {
  const f = await fixture(t), body = minimal();
  body.messages[1].content = 'x '.repeat(22500);
  const [expected] = await reference([body]);
  body.max_tokens = 32768 - 2048 - expected.prompt_tokens;
  assert.ok(body.max_tokens > 0 && body.max_tokens < 8192);
  const equal = run(f, body); successful(equal);
  assert.equal(equal.data.prompt_tokens + equal.data.requested_output + equal.data.reserve_tokens, 32768);
  body.max_tokens++;
  const over = run(f, body);
  assert.equal(over.status, 2); assert.equal(over.stderr, '{"error":"CONTEXT_LIMIT"}\n');
  assert.equal(over.data.fits, false);
  assert.equal(over.data.prompt_tokens + over.data.requested_output + over.data.reserve_tokens, 32769);
});

test('missing pins, mismatched assets/versions and false capacity assertions refuse', async t => {
  const f = await fixture(t);
  const original = structuredClone(f.manifest);
  for (const [mutate, code] of [
    [m => {delete m.tokenizer.files['config.json'];}, 'INVALID_MANIFEST'],
    [m => {m.tokenizer.files['tokenizer.json'] = '0'.repeat(64);}, 'PIN_MISMATCH'],
    [m => {m.tokenizer.files['chat_template.jinja'] = '1'.repeat(64);}, 'PIN_MISMATCH'],
    [m => {m.protocol.sha256 = '2'.repeat(64);}, 'PIN_MISMATCH'],
    [m => {m.versions.transformers = '0.0.0';}, 'VERSION_MISMATCH'],
    [m => {m.context_capacity = 65536;}, 'INVALID_MANIFEST'],
    [m => {m.reserve_tokens = 0;}, 'INVALID_MANIFEST']
  ]) {
    Object.assign(f.manifest, structuredClone(original)); mutate(f.manifest); await f.save();
    rejected(run(f), code);
  }
});

test('unknown formats, image URLs, duplicate JSON keys, unsafe output caps and oversized stdin refuse', async t => {
  const f = await fixture(t);
  for (const [body, code] of [
    [{...minimal(), messages: [{role: 'user', content: [{type: 'image_url', image_url: {url: 'https://invalid.example/SYNTHETIC_CANARY'}}]}]}, 'UNSUPPORTED_CONTENT'],
    [{...minimal(), messages: [{role: 'developer', content: 'not silently omitted'}]}, 'UNSUPPORTED_CONTENT'],
    [{...minimal(), messages: [{role: 'user', content: [{type: 'input_audio', data: 'ignored'}]}]}, 'UNSUPPORTED_CONTENT'],
    [{...minimal(), tokenizer: '/tmp/never-used'}, 'INVALID_REQUEST'],
    [{...minimal(), max_tokens: 8193}, 'INVALID_OUTPUT_LIMIT'],
    [{...minimal(), max_tokens: true}, 'INVALID_OUTPUT_LIMIT'],
    [{...minimal(), max_tokens: 0}, 'INVALID_OUTPUT_LIMIT'],
    [{...minimal(), max_completion_tokens: 6144}, 'INVALID_REQUEST']
  ]) rejected(run(f, body), code);
  rejected(run(f, null, {raw: '{"messages":[],"messages":[],"max_tokens":1}'}), 'INVALID_JSON');
  rejected(run(f, null, {raw: '{"messages":[],"max_tokens":1,"nomos":{"number":1e999}}'}), 'INVALID_JSON');
  rejected(run(f, null, {raw: ' '.repeat(1024 * 1024 + 1)}), 'INPUT_LIMIT');
  rejected(run(f, null, {raw: Buffer.from([0xff, 0xfe])}), 'INVALID_JSON');
});

test('manifest/asset symlinks, hardlinks and additional unpinned files refuse', async t => {
  const f = await fixture(t, true);
  await chmod(f.file, 0o644);
  rejected(run(f), 'UNSAFE_ASSET');
  await chmod(f.file, 0o600);
  const alias = path.join(f.temporary, 'manifest-link.json'); await symlink(f.file, alias);
  rejected(run({...f, file: alias}), 'UNSAFE_ASSET');
  await link(f.file, path.join(f.temporary, 'manifest-hardlink.json'));
  rejected(run(f), 'UNSAFE_ASSET');
  await rm(path.join(f.temporary, 'manifest-hardlink.json'));
  await writeFile(path.join(f.manifest.tokenizer.path, 'unlisted.json'), '{}');
  rejected(run(f), 'UNPINNED_ASSET');
  await rm(path.join(f.manifest.tokenizer.path, 'unlisted.json'));
  const config = path.join(f.manifest.tokenizer.path, 'config.json');
  await rm(config); await symlink(path.join(TOKENIZER, 'config.json'), config);
  rejected(run(f), 'UNPINNED_ASSET');
});

test('remote tokenizer code is refused even when its altered config is explicitly pinned', async t => {
  const f = await fixture(t, true);
  const file = path.join(f.manifest.tokenizer.path, 'tokenizer_config.json');
  const config = JSON.parse(await readFile(file, 'utf8'));
  config.auto_map = {AutoTokenizer: ['untrusted.Tokenizer', null]};
  const data = JSON.stringify(config); await writeFile(file, data);
  f.manifest.tokenizer.files['tokenizer_config.json'] = digest(data); await f.save();
  rejected(run(f), 'REMOTE_CODE_REFUSED');
});

test('the offline/CPU boundary blocks attempted socket/GPU imports and suppresses exception contents', {timeout: 40000}, async t => {
  const f = await fixture(t, true), original = await readFile(f.manifest.protocol.path, 'utf8');
  for (const [append, code] of [
    ['\nimport socket\nsocket.socket()\n', 'OFFLINE_ONLY'],
    ['\nimport torch\n', 'CPU_ONLY'],
    ['\nraise RuntimeError("SYNTHETIC_PROMPT_CANARY_NEVER_PRINT")\n', 'COUNT_UNAVAILABLE']
  ]) {
    const data = original + append; await writeFile(f.manifest.protocol.path, data);
    f.manifest.protocol.sha256 = digest(data); await f.save();
    rejected(run(f), code);
  }
});

test('inherited credentials are discarded and mutations after loading invalidate the measurement', {timeout: 30000}, async t => {
  const f = await fixture(t, true), original = await readFile(f.manifest.protocol.path, 'utf8');
  let data = original + '\nimport os\nassert "SYNTHETIC_HOST_SECRET" not in os.environ\n';
  await writeFile(f.manifest.protocol.path, data); f.manifest.protocol.sha256 = digest(data); await f.save();
  successful(run(f, minimal(), {env: {SYNTHETIC_HOST_SECRET: 'synthetic-not-a-real-secret'}}));
  data = original + '\nwith open(__file__, "ab") as altered:\n    altered.write(b"\\n# synthetic after-load mutation\\n")\n';
  await writeFile(f.manifest.protocol.path, data); f.manifest.protocol.sha256 = digest(data); await f.save();
  rejected(run(f), 'ASSET_CHANGED');
  data = original + `\nwith open(${JSON.stringify(f.manifest.protocol.path)}, "ab") as altered:\n    altered.write(b"\\n# synthetic source mutation\\n")\n`;
  await writeFile(f.manifest.protocol.path, data); f.manifest.protocol.sha256 = digest(data); await f.save();
  rejected(run(f), 'ASSET_CHANGED');
});

test('isolated flags and an existing manifest are mandatory; real source assets remain unchanged', async t => {
  const f = await fixture(t);
  for (const flags of [[], ['-I'], ['-B']]) {
    const result = spawnSync(PYTHON, [...flags, COUNTER, '--manifest', f.file], {input: 'SYNTHETIC_PROMPT_NOT_PARSED', encoding: 'utf8', env: clean, timeout: 5000});
    rejected(result, 'ISOLATED_RUNTIME_REQUIRED');
  }
  rejected(run({...f, file: path.join(f.temporary, 'missing-manifest.json')}), 'COUNT_UNAVAILABLE');
  const original = await pins();
  for (const name of ASSETS) assert.equal(digest(await readFile(path.join(TOKENIZER, name))), original.tokenizer.files[name]);
  assert.equal(digest(await readFile(PROTOCOL)), original.protocol.sha256);
});
