/** Browser-safe workspace contract. IDs are opaque selections, never paths. */
export const WORKSPACE_FILE_LIMITS = Object.freeze({
  entries: 128,
  visited: 512,
  depth: 8,
  pathBytes: 1024,
  fileBytes: 256 * 1024,
  totalBytes: 4 * 1024 * 1024,
  snapshots: 32,
  snapshotMs: 5 * 60 * 1000,
  concurrent: 4
});

export type WorkspaceFileEntry = {id: string; path: string; bytes: number};
export type WorkspaceFileList = {
  state: 'ready' | 'missing';
  snapshot: string | null;
  entries: WorkspaceFileEntry[];
  truncated: boolean;
  /** Only omissions actually encountered by the bounded scan; not a total. */
  omitted: number;
  limits: typeof WORKSPACE_FILE_LIMITS;
};
export type WorkspaceFilePreview = {path: string; bytes: number; text: string; sha256: string};

export function workspaceConversation(value: unknown): string | null {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value > 0 && value < 1e15 ? String(value) : null;
  // Canonical round-trip also excludes the final newline accepted by JS `$`.
  return typeof value === 'string' && /^[1-9][0-9]{0,14}$/.test(value) && String(Number(value)) === value ? value : null;
}
