import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {compile} from 'svelte/compiler';
import {createWorkspaceFiles, WorkspaceFilesError, WORKSPACE_FILE_LIMITS as LIMITS} from '../../src/lib/server/workspace-files.ts';
import {workspaceConversation} from '../../src/lib/agent/workspace.ts';

async function fixture(t: {after: (fn: () => Promise<void>) => void}) {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'glimmer-workspace-test-'));
  t.after(() => fs.rm(temporary, {recursive: true, force: true}));
  const root = path.join(temporary, 'workspaces');
  await fs.mkdir(root, {mode: 0o700});
  await fs.mkdir(path.join(root, '1'), {mode: 0o700});
  await fs.mkdir(path.join(root, '2'), {mode: 0o700});
  const file = (relative: string) => path.join(root, '1', relative);
  const write = (relative: string, data: string | Buffer) => fs.writeFile(file(relative), data, {mode: 0o600});
  return {temporary, root, file, write, viewer: createWorkspaceFiles(root)};
}
const refusal = (code: string) => (error: unknown) => {
  assert.ok(error instanceof WorkspaceFilesError);
  assert.equal(error.code, code);
  assert.doesNotMatch(error.message, /glimmer-workspace-test-|\/proc\/|ENOENT|ELOOP/);
  return true;
};
async function selected(f: Awaited<ReturnType<typeof fixture>>, name = 'safe.txt') {
  const listing = await f.viewer.list('1');
  const entry = listing.entries.find(item => item.path === name);
  assert.ok(listing.snapshot); assert.ok(entry);
  return {snapshot: listing.snapshot, entry};
}

test('numeric conversation identities are canonical and bounded', async t => {
  const f = await fixture(t);
  assert.equal(workspaceConversation(123), '123');
  assert.equal(workspaceConversation('999999999999999'), '999999999999999');
  for (const value of ['', '0', '01', '+1', '-1', '1.0', '1e2', ' 1', '1 ', '1\n', '1\r', '1/../2', '../1', '/1', '1\\2', '1\0', '1000000000000000', 0, -1, 1.5, 1e15, NaN, null, {}, []]) {
    assert.equal(workspaceConversation(value), null);
    await assert.rejects(f.viewer.list(value as string), refusal('INVALID_REQUEST'));
  }
  for (const root of ['/', '.', 'relative', f.root + '/', f.root + '/..', f.root + '\0']) {
    assert.throws(() => createWorkspaceFiles(root), refusal('UNSAFE_WORKSPACE'));
  }
});

test('absent roots and workspaces are explicit empty states without mkdir', async t => {
  const f = await fixture(t);
  const absent = path.join(f.temporary, 'not-created', 'workspaces');
  const result = await createWorkspaceFiles(absent).list('1');
  assert.deepEqual(result, {state: 'missing', snapshot: null, entries: [], truncated: false, omitted: 0, limits: LIMITS});
  await assert.rejects(fs.stat(path.join(f.temporary, 'not-created')), {code: 'ENOENT'});
  assert.equal((await f.viewer.list('999')).state, 'missing');
  await assert.rejects(fs.stat(path.join(f.root, '999')), {code: 'ENOENT'});
  const empty = await f.viewer.list('1');
  assert.equal(empty.state, 'ready'); assert.ok(empty.snapshot); assert.equal(empty.entries.length, 0);
});

test('lists sorted relative file names and reads unchanged UTF-8 bytes without mutation', async t => {
  const f = await fixture(t);
  await fs.mkdir(f.file('nested'), {mode: 0o700});
  const content = '<script>globalThis.PWNED=true</script>\nconst λ = "雪";\n';
  await f.write('nested/δ.mjs', content); await f.write('empty.txt', ''); await f.write('z.txt', 'z');
  const before = await fs.stat(f.file('nested/δ.mjs'), {bigint: true});
  const listing = await f.viewer.list(1);
  assert.deepEqual(listing.entries.map(entry => entry.path), ['empty.txt', 'nested/δ.mjs', 'z.txt']);
  assert.ok(listing.entries.every(entry => /^[a-f0-9]{48}$/.test(entry.id)));
  const entry = listing.entries[1];
  const result = await f.viewer.read('1', listing.snapshot!, entry.id);
  assert.deepEqual(result, {path: entry.path, bytes: Buffer.byteLength(content), text: content, sha256: createHash('sha256').update(content).digest('hex')});
  assert.equal((await f.viewer.read('1', listing.snapshot!, listing.entries[0].id)).text, '');
  const after = await fs.stat(f.file('nested/δ.mjs'), {bigint: true});
  assert.deepEqual([after.ino, after.size, after.mtimeNs, after.ctimeNs], [before.ino, before.size, before.mtimeNs, before.ctimeNs]);
  assert.equal((globalThis as Record<string, unknown>).PWNED, undefined);
});

test('only issued entry IDs from that conversation and snapshot are accepted', async t => {
  const f = await fixture(t);
  await f.write('safe.txt', 'SAFE'); await fs.writeFile(path.join(f.root, '2', 'safe.txt'), 'OTHER', {mode: 0o600});
  const {snapshot, entry} = await selected(f);
  await assert.rejects(f.viewer.read('2', snapshot, entry.id), refusal('STALE_SNAPSHOT'));
  for (const value of ['safe.txt', '../outside', f.file('safe.txt'), '/etc/passwd', '', 'a'.repeat(49), 'a'.repeat(48) + '\n']) {
    await assert.rejects(f.viewer.read('1', snapshot, value), refusal('INVALID_REQUEST'));
    await assert.rejects(f.viewer.read('1', value, entry.id), refusal('INVALID_REQUEST'));
  }
  await assert.rejects(f.viewer.read('1', 'a'.repeat(48), entry.id), refusal('STALE_SNAPSHOT'));
  await assert.rejects(f.viewer.read('1', snapshot, 'b'.repeat(48)), refusal('INVALID_REQUEST'));
  const other = await f.viewer.list('2');
  await assert.rejects(f.viewer.read('1', snapshot, other.entries[0].id), refusal('INVALID_REQUEST'));
  // Mutating returned list data cannot mutate the server's authority records.
  entry.path = '../outside'; entry.bytes = 0;
  assert.equal((await f.viewer.read('1', snapshot, entry.id)).text, 'SAFE');
});

test('refresh, expiry and snapshot retention invalidate old selections', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  const first = await selected(f); const second = await selected(f);
  assert.notEqual(first.snapshot, second.snapshot);
  await assert.rejects(f.viewer.read('1', first.snapshot, first.entry.id), refusal('STALE_SNAPSHOT'));
  const now = Date.now();
  const time = t.mock.method(Date, 'now', () => now + LIMITS.snapshotMs + 1);
  await assert.rejects(f.viewer.read('1', second.snapshot, second.entry.id), refusal('STALE_SNAPSHOT'));
  time.mock.restore();
  const retained = await selected(f);
  for (let id = 3; id < 3 + LIMITS.snapshots; id++) {
    await fs.mkdir(path.join(f.root, String(id)), {mode: 0o700});
    await f.viewer.list(id);
  }
  await assert.rejects(f.viewer.read('1', retained.snapshot, retained.entry.id), refusal('STALE_SNAPSHOT'));
});

test('symlinks, hardlinks, FIFOs and reserved staging names are omitted without reading decoys', async t => {
  const f = await fixture(t);
  const decoy = path.join(f.temporary, 'decoy.txt');
  await fs.writeFile(decoy, 'SYNTHETIC_OUTSIDE_CANARY', {mode: 0o600});
  await f.write('safe.txt', 'SAFE');
  await fs.symlink(decoy, f.file('linked.txt'));
  await fs.symlink(f.temporary, f.file('linked-directory'));
  await fs.link(decoy, f.file('hardlinked.txt'));
  await f.write('.glimmer-0123456789abcdef0123456789abcdef', 'UNCOMMITTED');
  assert.equal(spawnSync('/usr/bin/mkfifo', [f.file('pipe')], {timeout: 2000}).status, 0);
  const listing = await f.viewer.list('1');
  assert.deepEqual(listing.entries.map(item => item.path), ['safe.txt']);
  assert.equal(listing.omitted, 5);
  assert.equal((await f.viewer.read('1', listing.snapshot!, listing.entries[0].id)).text, 'SAFE');
  assert.equal(await fs.readFile(decoy, 'utf8'), 'SYNTHETIC_OUTSIDE_CANARY');
});

test('symlinked root ancestors and conversation roots fail closed, including missing targets', async t => {
  const f = await fixture(t);
  const link = path.join(f.temporary, 'alias'); await fs.symlink(f.root, link);
  await assert.rejects(createWorkspaceFiles(link).list('1'), refusal('UNSAFE_WORKSPACE'));
  await assert.rejects(createWorkspaceFiles(link + '/missing').list('1'), refusal('UNSAFE_WORKSPACE'));
  await fs.symlink(f.file(''), path.join(f.root, '3'));
  await assert.rejects(f.viewer.list('3'), refusal('UNSAFE_WORKSPACE'));
});

test('private owner roots are required and foreign or writable entries are omitted', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  await fs.chmod(f.root, 0o755);
  await assert.rejects(f.viewer.list('1'), refusal('UNSAFE_WORKSPACE'));
  await fs.chmod(f.root, 0o700); await fs.chmod(f.file(''), 0o750);
  await assert.rejects(f.viewer.list('1'), refusal('UNSAFE_WORKSPACE'));
  await fs.chmod(f.file(''), 0o700); await fs.chmod(f.file('safe.txt'), 0o666);
  await fs.mkdir(f.file('writable'), {mode: 0o777}); await fs.chmod(f.file('writable'), 0o777);
  await f.write('writable/hidden.txt', 'HIDDEN');
  const listing = await f.viewer.list('1');
  assert.equal(listing.entries.length, 0); assert.equal(listing.omitted, 2);
  const actualUid = process.getuid!();
  t.mock.method(process as NodeJS.Process & {getuid: () => number}, 'getuid', () => actualUid + 1);
  await assert.rejects(f.viewer.list('1'), refusal('UNSAFE_WORKSPACE'));
});

test('invalid UTF-8, binary controls and oversized files cannot be previewed', async t => {
  const f = await fixture(t);
  await f.write('bad-utf8', Buffer.from([0xff, 0xfe]));
  await f.write('binary', Buffer.from([65, 0, 66]));
  await f.write('escape', '\x1b[31munsafe');
  await f.write('too-big', Buffer.alloc(LIMITS.fileBytes + 1, 65));
  await f.write('at-limit', Buffer.alloc(LIMITS.fileBytes, 65));
  await f.write('bom', '\ufeffUTF-8');
  const listing = await f.viewer.list('1');
  assert.equal(listing.omitted, 1); assert.ok(!listing.entries.some(item => item.path === 'too-big'));
  for (const name of ['bad-utf8', 'binary', 'escape']) {
    await assert.rejects(f.viewer.read('1', listing.snapshot!, listing.entries.find(item => item.path === name)!.id), refusal('UNSUPPORTED_FILE'));
  }
  assert.equal((await f.viewer.read('1', listing.snapshot!, listing.entries.find(item => item.path === 'at-limit')!.id)).bytes, LIMITS.fileBytes);
  assert.equal((await f.viewer.read('1', listing.snapshot!, listing.entries.find(item => item.path === 'bom')!.id)).text, '\ufeffUTF-8');
});

test('entry and visited-node limits bound scans, including large unsafe-only directories', async t => {
  const f = await fixture(t);
  await Promise.all(Array.from({length: LIMITS.entries + 10}, (_, index) => f.write(`file-${index}`, 'x')));
  let listing = await f.viewer.list('1');
  assert.equal(listing.entries.length, LIMITS.entries); assert.equal(listing.truncated, true);
  const excluded = path.join(f.root, '2');
  await Promise.all(Array.from({length: LIMITS.visited + 10}, (_, index) => fs.symlink('../missing', path.join(excluded, `link-${index}`))));
  listing = await f.viewer.list('2');
  assert.equal(listing.entries.length, 0); assert.equal(listing.omitted, LIMITS.visited); assert.equal(listing.truncated, true);
});

test('listed total bytes and depth are bounded independently of entry count', async t => {
  const f = await fixture(t);
  const count = LIMITS.totalBytes / LIMITS.fileBytes;
  for (let index = 0; index < count + 2; index++) await f.write(`large-${index}`, Buffer.alloc(LIMITS.fileBytes, 65));
  const listing = await f.viewer.list('1');
  assert.equal(listing.entries.reduce((sum, entry) => sum + entry.bytes, 0), LIMITS.totalBytes);
  assert.equal(listing.entries.length, count); assert.equal(listing.omitted, 2); assert.equal(listing.truncated, true);
  let directory = path.join(f.root, '2');
  for (let depth = 1; depth <= LIMITS.depth + 1; depth++) {
    directory = path.join(directory, `d${depth}`); await fs.mkdir(directory, {mode: 0o700});
    await fs.writeFile(path.join(directory, 'leaf.txt'), 'depth', {mode: 0o600});
  }
  const nested = await f.viewer.list('2');
  assert.ok(nested.entries.every(entry => entry.path.split('/').length <= LIMITS.depth));
  assert.equal(nested.entries.length, LIMITS.depth - 1); assert.equal(nested.truncated, true);
});

test('path byte bounds and undecodable or misleading names are enforced', async t => {
  const f = await fixture(t);
  for (const name of ['bad\\name', 'control\nname', 'bidi\u202ename', 'replacement\ufffdname']) await f.write(name, 'hidden');
  await fs.writeFile(Buffer.concat([Buffer.from(f.file('')), Buffer.from('/raw-'), Buffer.from([0xff])]), 'raw', {mode: 0o600});
  let directory = f.file('');
  for (let index = 0; index < 5; index++) {
    directory = path.join(directory, String(index).repeat(220)); await fs.mkdir(directory, {mode: 0o700});
  }
  await fs.writeFile(path.join(directory, 'hidden'), 'long', {mode: 0o600});
  const listing = await f.viewer.list('1');
  assert.equal(listing.entries.length, 0); assert.equal(listing.omitted, 6);
});

test('changed content, restored mtime, added links and replaced files invalidate selections', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  let saved = await selected(f);
  const before = await fs.stat(f.file('safe.txt'));
  await f.write('safe.txt', 'EVIL'); await fs.utimes(f.file('safe.txt'), before.atime, before.mtime);
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  saved = await selected(f);
  await fs.rename(f.file('safe.txt'), f.file('old.txt')); await f.write('safe.txt', 'SWAP');
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  saved = await selected(f);
  await fs.link(f.file('safe.txt'), path.join(f.temporary, 'linked-after-list'));
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
});

test('directory, workspace and configured-root substitutions invalidate snapshots', async t => {
  const f = await fixture(t);
  await fs.mkdir(f.file('nested'), {mode: 0o700}); await f.write('nested/safe.txt', 'SAFE');
  let saved = await selected(f, 'nested/safe.txt');
  await fs.rename(f.file('nested'), f.file('old'));
  await fs.mkdir(f.file('nested'), {mode: 0o700}); await f.write('nested/safe.txt', 'SWAP');
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  saved = await selected(f, 'nested/safe.txt');
  await fs.rename(f.file(''), path.join(f.root, 'old-workspace'));
  await fs.mkdir(f.file(''), {mode: 0o700});
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  await f.write('safe.txt', 'NEW'); saved = await selected(f);
  await fs.rename(f.root, path.join(f.temporary, 'old-root')); await fs.mkdir(f.root, {mode: 0o700});
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
});

test('a directory listing changed after enumeration is refused', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  const original = fs.opendir.bind(fs);
  t.mock.method(fs, 'opendir', async (...args: Parameters<typeof fs.opendir>) => {
    const scan = await original(...args);
    const read = scan.read.bind(scan); let changed = false;
    scan.read = (async () => {
      const item = await read();
      if (!item && !changed) {changed = true; await f.write('added-after-scan', 'NEW');}
      return item;
    }) as typeof scan.read;
    return scan;
  });
  await assert.rejects(f.viewer.list('1'), refusal('STALE_SNAPSHOT'));
});

test('file substitution after O_PATH pinning is refused without reading the replacement', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  const saved = await selected(f), decoy = path.join(f.temporary, 'decoy');
  await fs.writeFile(decoy, 'SYNTHETIC_OUTSIDE_CANARY', {mode: 0o600});
  const original = fs.open.bind(fs); let swapped = false;
  t.mock.method(fs, 'open', async (...args: Parameters<typeof fs.open>) => {
    const handle = await original(...args);
    if (!swapped && String(args[0]).endsWith('/safe.txt')) {
      swapped = true;
      await fs.rename(f.file('safe.txt'), f.file('old.txt')); await fs.symlink(decoy, f.file('safe.txt'));
    }
    return handle;
  });
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  assert.equal(swapped, true);
});

test('in-place mutation during a bounded read is detected before any result is returned', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  const saved = await selected(f), original = fs.open.bind(fs); let changed = false;
  t.mock.method(fs, 'open', async (...args: Parameters<typeof fs.open>) => {
    const handle = await original(...args);
    if (/^\/proc\/self\/fd\/[0-9]+$/.test(String(args[0]))) {
      const read = handle.read.bind(handle);
      handle.read = (async (...readArgs: Parameters<typeof handle.read>) => {
        const result = await read(...readArgs);
        if (!changed) {changed = true; await f.write('safe.txt', 'EVIL');}
        return result;
      }) as typeof handle.read;
    }
    return handle;
  });
  await assert.rejects(f.viewer.read('1', saved.snapshot, saved.entry.id), refusal('STALE_SNAPSHOT'));
  assert.equal(changed, true);
});

test('concurrency is bounded and completed operations release every held descriptor', async t => {
  const f = await fixture(t); await f.write('safe.txt', 'SAFE');
  const original = fs.opendir.bind(fs);
  let release!: () => void;
  const pause = new Promise<void>(resolve => {release = resolve;});
  const blocked = t.mock.method(fs, 'opendir', async (...args: Parameters<typeof fs.opendir>) => {await pause; return original(...args);});
  const active = Array.from({length: LIMITS.concurrent}, () => f.viewer.list('1'));
  await assert.rejects(f.viewer.list('1'), refusal('BUSY'));
  release(); await Promise.all(active); blocked.mock.restore();
  const before = (await fs.readdir('/proc/self/fd')).length;
  for (let index = 0; index < 12; index++) {
    const saved = await selected(f);
    await f.viewer.read('1', saved.snapshot, saved.entry.id);
    await assert.rejects(f.viewer.read('1', saved.snapshot, '0'.repeat(48)), refusal('INVALID_REQUEST'));
  }
  const after = (await fs.readdir('/proc/self/fd')).length;
  assert.equal(after, before);
});

test('same-device bind mounts of files, nested directories and conversations are refused', async t => {
  const f = await fixture(t);
  await f.write('safe.txt', 'SAFE'); await f.write('bound.txt', 'placeholder');
  await fs.mkdir(f.file('mounted'), {mode: 0o700});
  const outside = path.join(f.temporary, 'outside'); await fs.mkdir(outside, {mode: 0o700});
  await fs.writeFile(path.join(outside, 'decoy.txt'), 'SYNTHETIC_MOUNT_CANARY', {mode: 0o600});
  const source = fileURLToPath(new URL('../../src/lib/', import.meta.url));
  const code = `
    import assert from 'node:assert/strict';
    import {createWorkspaceFiles} from '/source/server/workspace-files.ts';
    const viewer = createWorkspaceFiles('/fixture/workspaces');
    const listing = await viewer.list('1');
    assert.deepEqual(listing.entries.map(entry => entry.path), ['safe.txt']);
    assert.equal(listing.omitted, 2);
    assert.equal((await viewer.read('1', listing.snapshot, listing.entries[0].id)).text, 'SAFE');
    await assert.rejects(viewer.list('2'), error => error.code === 'UNSAFE_WORKSPACE');
    console.log('BIND MOUNT BOUNDARY VERIFIED');
  `;
  const result = spawnSync('/usr/bin/bwrap', [
    '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
    '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
    '--ro-bind', process.execPath, '/node', '--ro-bind', source, '/source',
    '--bind', f.temporary, '/fixture',
    '--ro-bind', path.join(outside, 'decoy.txt'), '/fixture/workspaces/1/bound.txt',
    '--ro-bind', outside, '/fixture/workspaces/1/mounted',
    '--ro-bind', outside, '/fixture/workspaces/2',
    '--proc', '/proc', '--dev', '/dev', '--chdir', '/', '--', '/node', '--input-type=module', '-e', code
  ], {encoding: 'utf8', timeout: 10000, maxBuffer: 32768});
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /BIND MOUNT BOUNDARY VERIFIED/);
});

test('Svelte viewer compiles with inert text, GET-only token selection and bounded refreshes', async () => {
  const source = await fs.readFile(new URL('../../src/lib/components/WorkspaceFiles.svelte', import.meta.url), 'utf8');
  const result = compile(source, {filename: 'WorkspaceFiles.svelte', generate: 'client'});
  assert.deepEqual(result.warnings, []);
  assert.doesNotMatch(source, /\{@html|{@render|<iframe|<object|<embed|<script[^>]+src=|innerHTML|eval\(|new Function|localStorage|setInterval/);
  assert.match(source, /method: 'GET'/);
  assert.match(source, /new URLSearchParams\(\{conversation: id, snapshot, entry: entry\.id\}\)/);
  assert.doesNotMatch(source, /URLSearchParams\([^\n]*(?:path|entry\.path)/);
  assert.match(source, /\{preview\.text \|\| '\(Empty file\)'\}/);
  assert.match(source, /<bdi>\{entry\.path\}<\/bdi>/);
  assert.match(source, /void refreshKey/);
  assert.match(source, /setTimeout\(\(\) => void refresh\(id\), 180\)/);
  assert.match(source, /AbortSignal\.timeout\(10000\)/);
  assert.match(source, /state === 'missing'/);
  assert.match(source, /data-testid="workspace-refresh"/);
  assert.doesNotMatch(source, /:global\(/);
});
