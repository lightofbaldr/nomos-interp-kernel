/** Read-only Linux sandbox viewer. No browser-controlled filesystem paths.
 *
 * The caller supplies the actual conversation parent, i.e.
 * `${database}.workspaces/workspaces`, and separately checks conversation access.
 * Every component is pinned with O_PATH|O_NOFOLLOW. /proc/self/fd is used only
 * with descriptors held by this operation, never with user-provided numbers.
 * Pinning before reopening prevents even opening a substituted device/FIFO for
 * reading. Mount IDs, not just st_dev, reject same-device bind mounts.
 *
 * This is not isolation from a privileged or same-UID host adversary. Like the
 * file broker, it assumes the operator controls the host and /proc. Untrusted
 * sandbox writers may change their own files; inconsistent views fail closed.
 */
import fs from 'node:fs/promises';
import {constants, type BigIntStats} from 'node:fs';
import type {FileHandle} from 'node:fs/promises';
import path from 'node:path';
import {createHash, randomBytes} from 'node:crypto';
import {WORKSPACE_FILE_LIMITS as LIMITS, workspaceConversation} from '../agent/workspace.ts';
import type {WorkspaceFileEntry, WorkspaceFileList, WorkspaceFilePreview} from '../agent/workspace.ts';

export {WORKSPACE_FILE_LIMITS} from '../agent/workspace.ts';
type Code = 'INVALID_REQUEST' | 'UNAVAILABLE' | 'UNSAFE_WORKSPACE' | 'STALE_SNAPSHOT' | 'UNSUPPORTED_FILE' | 'BUSY';
const ERRORS: Record<Code, {status: number; message: string}> = {
  INVALID_REQUEST: {status: 400, message: 'Select a listed file from a valid conversation.'},
  UNAVAILABLE: {status: 503, message: 'The read-only workspace viewer is unavailable on this server.'},
  UNSAFE_WORKSPACE: {status: 409, message: 'This workspace cannot be safely opened. No files were read.'},
  STALE_SNAPSHOT: {status: 409, message: 'The workspace or file changed. Refresh files and select again.'},
  UNSUPPORTED_FILE: {status: 415, message: 'Preview requires a bounded, regular UTF-8 text file without binary controls.'},
  BUSY: {status: 429, message: 'The workspace viewer is busy. Try refreshing shortly.'}
};

export class WorkspaceFilesError extends Error {
  readonly code: Code;
  readonly status: number;
  constructor(code: Code) {
    super(ERRORS[code].message);
    this.name = 'WorkspaceFilesError';
    this.code = code;
    this.status = ERRORS[code].status;
  }
}
function fail(code: Code): never {throw new WorkspaceFilesError(code);}
const O_PATH = 0x200000; // Linux UAPI; Node does not export O_PATH.
const TOKEN = /^[a-f0-9]{48}$/;
const token = () => randomBytes(24).toString('hex');
type Held = {handle: FileHandle; stat: BigIntStats; mount: string};
type Recorded = {parts: string[]; stamp: string};
type Listed = Recorded & {entry: WorkspaceFileEntry};
type Snapshot = {conversation: string; created: number; anchors: string[]; directories: Map<string, Recorded>; files: Map<string, Listed>};
type Anchor = {held: Held[]; workspace: Held};
const proc = (held: Held) => `/proc/self/fd/${held.handle.fd}`;
const errno = (error: unknown, code: string) => (error as NodeJS.ErrnoException)?.code === code;
const identity = (held: Held) => [held.stat.dev, held.stat.ino, held.stat.mode, held.stat.uid, held.stat.gid, held.mount].join(':');
const stamp = (held: Held) => [identity(held), held.stat.nlink, held.stat.size, held.stat.mtimeNs, held.stat.ctimeNs].join(':');
const current = async (held: Held): Promise<Held> => ({...held, stat: await held.handle.stat({bigint: true})});
const close = async (held: Held[]) => {await Promise.all(held.map(item => item.handle.close()));};

async function mountId(handle: FileHandle): Promise<string> {
  const info = await fs.open(`/proc/self/fdinfo/${handle.fd}`, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const data = Buffer.alloc(4097);
    const {bytesRead} = await info.read(data, 0, data.length, 0);
    const match = bytesRead <= 4096 && /^mnt_id:\s*([0-9]+)$/m.exec(data.subarray(0, bytesRead).toString('ascii'));
    return match ? match[1] : fail('UNAVAILABLE');
  } finally {await info.close();}
}

async function pin(target: string, directory = false): Promise<Held> {
  const handle = await fs.open(target, O_PATH | constants.O_NOFOLLOW | (directory ? constants.O_DIRECTORY : 0));
  try {return {handle, stat: await handle.stat({bigint: true}), mount: await mountId(handle)};}
  catch (error) {await handle.close(); throw error;}
}

function owner(held: Held, privateDirectory = false): boolean {
  return held.stat.uid === BigInt(process.getuid!()) && (held.stat.mode & (privateDirectory ? 0o077n : 0o022n)) === 0n;
}
function regular(held: Held): boolean {
  return held.stat.isFile() && held.stat.nlink === 1n && owner(held) && held.stat.size >= 0n && held.stat.size <= BigInt(LIMITS.fileBytes);
}
function validName(name: string): boolean {
  // Reject undecodable directory names rather than round-tripping U+FFFD to a
  // different file. Reserved .glimmer- files are uncommitted broker temporaries.
  return !!name && name !== '.' && name !== '..' && !name.startsWith('.glimmer-') &&
    !/[\\/\x00-\x1f\x7f\ufffd\u202a-\u202e\u2066-\u2069]/.test(name) && Buffer.byteLength(name) <= 255;
}

export function createWorkspaceFiles(root: string) {
  if (process.platform !== 'linux' || !process.getuid || !constants.O_NOFOLLOW || !constants.O_DIRECTORY) fail('UNAVAILABLE');
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.normalize(root) !== root || root.endsWith('/') || root.length > 4096 || root.includes('\0')) fail('UNSAFE_WORKSPACE');
  const rootParts = root.split('/').slice(1);
  if (rootParts.length > 64) fail('UNSAFE_WORKSPACE');
  const snapshots = new Map<string, Snapshot>();
  let active = 0;

  function prune(conversation?: string) {
    const now = Date.now();
    for (const [id, snapshot] of snapshots) {
      if (now < snapshot.created || now - snapshot.created >= LIMITS.snapshotMs || snapshot.conversation === conversation) snapshots.delete(id);
    }
  }
  async function bounded<T>(run: () => Promise<T>): Promise<T> {
    if (active >= LIMITS.concurrent) fail('BUSY');
    active++;
    try {return await run();}
    catch (error) {if (error instanceof WorkspaceFilesError) throw error; return fail('UNSAFE_WORKSPACE');}
    finally {active--;}
  }
  async function anchor(conversation: string): Promise<Anchor> {
    const held: Held[] = [];
    try {
      held.push(await pin('/', true));
      for (const name of rootParts) held.push(await pin(`${proc(held.at(-1)!)}/${name}`, true));
      const parent = held.at(-1)!;
      if (!owner(parent, true)) fail('UNSAFE_WORKSPACE');
      const workspace = await pin(`${proc(parent)}/${conversation}`, true);
      held.push(workspace);
      if (!owner(workspace, true) || workspace.mount !== parent.mount || workspace.stat.dev !== parent.stat.dev) fail('UNSAFE_WORKSPACE');
      return {held, workspace};
    } catch (error) {await close(held); throw error;}
  }
  function anchors(value: Anchor) {return value.held.map(identity);}
  async function verifyAnchor(value: Anchor, expected: string[]) {
    if (value.held.length !== expected.length) fail('STALE_SNAPSHOT');
    for (const [index, held] of value.held.entries()) {
      if (identity(await current(held)) !== expected[index]) fail('STALE_SNAPSHOT');
    }
  }
  async function resolveDirectory(value: Anchor, parts: string[], records: Map<string, Recorded>) {
    const held: Held[] = [];
    let directory = value.workspace;
    try {
      for (let index = 0; index <= parts.length; index++) {
        if (index) {
          directory = await pin(`${proc(directory)}/${parts[index - 1]}`, true);
          held.push(directory);
        }
        const record = records.get(parts.slice(0, index).join('/'));
        if (!record || directory.mount !== value.workspace.mount || !owner(directory) || stamp(await current(directory)) !== record.stamp) fail('STALE_SNAPSHOT');
      }
      return {directory, held};
    } catch (error) {await close(held); throw error;}
  }
  async function verifySelection(conversation: string, snapshot: Snapshot, selected: Listed) {
    const value = await anchor(conversation);
    let directories: Held[] = [], file: Held | undefined;
    try {
      await verifyAnchor(value, snapshot.anchors);
      const result = await resolveDirectory(value, selected.parts.slice(0, -1), snapshot.directories);
      directories = result.held;
      file = await pin(`${proc(result.directory)}/${selected.parts.at(-1)!}`);
      if (!regular(file) || file.mount !== value.workspace.mount || stamp(file) !== selected.stamp) fail('STALE_SNAPSHOT');
    } finally {await close([...value.held, ...directories, ...(file ? [file] : [])]);}
  }

  return {
    async list(conversation: string | number): Promise<WorkspaceFileList> {
      const id = workspaceConversation(conversation);
      if (!id) fail('INVALID_REQUEST');
      return bounded(async () => {
        prune();
        let value: Anchor;
        try {value = await anchor(id);}
        catch (error) {
          if (!errno(error, 'ENOENT')) throw error;
          prune(id);
          return {state: 'missing', snapshot: null, entries: [], truncated: false, omitted: 0, limits: LIMITS};
        }
        const entries: WorkspaceFileEntry[] = [], files = new Map<string, Listed>(), directories = new Map<string, Recorded>();
        let visited = 0, total = 0, omitted = 0, truncated = false;
        const started = Date.now();
        const snapshot: Snapshot = {conversation: id, created: started, anchors: anchors(value), directories, files};
        async function walk(directory: Held, parts: string[]) {
          const before = stamp(directory);
          directories.set(parts.join('/'), {parts, stamp: before});
          // opendir buffers only a small fixed number; readdir would materialize
          // an attacker-controlled directory before the entry bound can apply.
          const scan = await fs.opendir(proc(directory), {bufferSize: 16});
          try {
            while (true) {
              if (visited >= LIMITS.visited || entries.length >= LIMITS.entries || Date.now() - started > 2000) {truncated = true; break;}
              const item = await scan.read();
              if (!item) break;
              visited++;
              const names = [...parts, item.name];
              if (!validName(item.name) || Buffer.byteLength(names.join('/')) > LIMITS.pathBytes) {omitted++; continue;}
              let child: Held | undefined;
              try {
                child = await pin(`${proc(directory)}/${item.name}`);
                if (child.mount !== value.workspace.mount || child.stat.dev !== value.workspace.stat.dev || !owner(child)) {omitted++; continue;}
                if (child.stat.isDirectory()) {
                  if (names.length >= LIMITS.depth) {omitted++; truncated = true; continue;}
                  await walk(child, names);
                } else if (regular(child)) {
                  const bytes = Number(child.stat.size);
                  if (total + bytes > LIMITS.totalBytes) {omitted++; truncated = true; continue;}
                  const entry = {id: token(), path: names.join('/'), bytes};
                  entries.push(entry); total += bytes;
                  files.set(entry.id, {entry, parts: names, stamp: stamp(child)});
                } else {omitted++;}
              } catch (error) {
                if (errno(error, 'ENOENT') || errno(error, 'ENOTDIR') || errno(error, 'ELOOP')) fail('STALE_SNAPSHOT');
                throw error;
              } finally {if (child) await child.handle.close();}
              if (truncated && (visited >= LIMITS.visited || entries.length >= LIMITS.entries)) break;
            }
          } finally {await scan.close();}
          if (stamp(await current(directory)) !== before) fail('STALE_SNAPSHOT');
        }
        try {
          await walk(value.workspace, []);
          const fresh = await anchor(id);
          try {
            await verifyAnchor(fresh, snapshot.anchors);
            if (stamp(fresh.workspace) !== directories.get('')!.stamp) fail('STALE_SNAPSHOT');
          } finally {await close(fresh.held);}
          entries.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
          prune(id);
          while (snapshots.size >= LIMITS.snapshots) snapshots.delete(snapshots.keys().next().value!);
          const key = token(); snapshots.set(key, snapshot);
          // Public objects are detached from the server's authority records.
          return {state: 'ready', snapshot: key, entries: entries.map(entry => ({...entry})), truncated, omitted, limits: LIMITS};
        } finally {await close(value.held);}
      });
    },

    async read(conversation: string | number, snapshotId: string, entryId: string): Promise<WorkspaceFilePreview> {
      const id = workspaceConversation(conversation);
      if (!id || typeof snapshotId !== 'string' || snapshotId.length !== 48 || !TOKEN.test(snapshotId) || typeof entryId !== 'string' || entryId.length !== 48 || !TOKEN.test(entryId)) fail('INVALID_REQUEST');
      return bounded(async () => {
        prune();
        const snapshot = snapshots.get(snapshotId);
        if (!snapshot || snapshot.conversation !== id) fail('STALE_SNAPSHOT');
        const selected = snapshot.files.get(entryId);
        if (!selected) fail('INVALID_REQUEST');
        let value: Anchor | undefined, directories: Held[] = [], pinned: Held | undefined, reader: FileHandle | undefined;
        try {
          value = await anchor(id);
          await verifyAnchor(value, snapshot.anchors);
          const resolved = await resolveDirectory(value, selected.parts.slice(0, -1), snapshot.directories);
          directories = resolved.held;
          pinned = await pin(`${proc(resolved.directory)}/${selected.parts.at(-1)!}`);
          if (!regular(pinned) || pinned.mount !== value.workspace.mount || stamp(pinned) !== selected.stamp) fail('STALE_SNAPSHOT');
          // The only deliberate magic-link follow: reopen our *already pinned*
          // regular file, not its mutable workspace pathname. O_NOFOLLOW here
          // would reject our own proc descriptor, not improve its boundary.
          reader = await fs.open(proc(pinned), constants.O_RDONLY | constants.O_NONBLOCK);
          const opened: Held = {handle: reader, stat: await reader.stat({bigint: true}), mount: await mountId(reader)};
          if (!regular(opened) || stamp(opened) !== selected.stamp) fail('STALE_SNAPSHOT');
          const data = Buffer.alloc(selected.entry.bytes + 1);
          let used = 0;
          while (used < data.length) {
            const {bytesRead} = await reader.read(data, used, data.length - used, used);
            if (!bytesRead) break;
            used += bytesRead;
          }
          if (used !== selected.entry.bytes || stamp(await current(opened)) !== selected.stamp || stamp(await current(pinned)) !== selected.stamp) fail('STALE_SNAPSHOT');
          await verifySelection(id, snapshot, selected);
          if (snapshots.get(snapshotId) !== snapshot || Date.now() < snapshot.created || Date.now() - snapshot.created >= LIMITS.snapshotMs) fail('STALE_SNAPSHOT');
          let text: string;
          try {text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(data.subarray(0, used));}
          catch {return fail('UNSUPPORTED_FILE');}
          if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text)) fail('UNSUPPORTED_FILE');
          return {path: selected.entry.path, bytes: used, text, sha256: createHash('sha256').update(data.subarray(0, used)).digest('hex')};
        } catch (error) {
          if (error instanceof WorkspaceFilesError && ['UNSUPPORTED_FILE', 'UNAVAILABLE'].includes(error.code)) throw error;
          return fail('STALE_SNAPSHOT');
        } finally {
          if (reader) await reader.close();
          await close([...(value?.held ?? []), ...directories, ...(pinned ? [pinned] : [])]);
        }
      });
    }
  };
}
