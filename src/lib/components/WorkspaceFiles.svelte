<script lang="ts">
  import {untrack} from 'svelte';
  import {WORKSPACE_FILE_LIMITS, workspaceConversation} from '../agent/workspace';
  import type {WorkspaceFileEntry, WorkspaceFileList, WorkspaceFilePreview} from '../agent/workspace';

  let {conversation, refreshKey = ''}: {conversation: string | number | null; refreshKey?: string | number} = $props();
  let listing = $state<WorkspaceFileList | null>(null);
  let preview = $state<WorkspaceFilePreview | null>(null);
  let selected = $state('');
  let loading = $state(false), reading = $state(false);
  let error = $state(''), previewError = $state('');
  let listRequest: AbortController | undefined, readRequest: AbortController | undefined;
  let revision = 0;
  let autoRefresh: ReturnType<typeof setTimeout> | undefined;
  const heading = $props.id();

  function cancel() {
    revision++;
    listRequest?.abort(); readRequest?.abort();
  }
  function reset() {
    cancel(); listing = null; preview = null; selected = '';
    error = ''; previewError = ''; loading = false; reading = false;
  }
  async function request(query: URLSearchParams, controller: AbortController) {
    const response = await fetch(`/api/pi/files?${query}`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {Accept: 'application/json'},
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)])
    });
    const result = await response.json();
    if (!response.ok) throw Error(typeof result?.error === 'string' && result.error.length <= 240 ? result.error : 'Workspace files are unavailable.');
    return result;
  }
  async function refresh(value: string | number | null = conversation) {
    clearTimeout(autoRefresh);
    reset();
    const id = workspaceConversation(value);
    if (!id) return;
    const currentRevision = revision, controller = new AbortController();
    listRequest = controller; loading = true;
    try {
      const result: WorkspaceFileList = await request(new URLSearchParams({conversation: id}), controller);
      if (controller.signal.aborted || revision !== currentRevision) return;
      if (!['ready', 'missing'].includes(result?.state) || !Array.isArray(result.entries) || result.entries.length > WORKSPACE_FILE_LIMITS.entries ||
          (result.state === 'ready' && (typeof result.snapshot !== 'string' || result.snapshot.length !== 48 || !/^[a-f0-9]{48}$/.test(result.snapshot))) ||
          result.entries.some(entry => !entry || typeof entry.id !== 'string' || entry.id.length !== 48 || !/^[a-f0-9]{48}$/.test(entry.id) || typeof entry.path !== 'string' || entry.path.length > WORKSPACE_FILE_LIMITS.pathBytes || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || entry.bytes > WORKSPACE_FILE_LIMITS.fileBytes)) {
        throw Error('The workspace listing was invalid. Refresh to try again.');
      }
      listing = result;
    } catch (reason) {
      if (!controller.signal.aborted && revision === currentRevision) error = reason instanceof Error ? reason.message : 'Workspace files are unavailable.';
    } finally {if (revision === currentRevision) loading = false;}
  }
  async function select(entry: WorkspaceFileEntry) {
    const id = workspaceConversation(conversation), snapshot = listing?.snapshot;
    if (!id || !snapshot || !listing?.entries.some(item => item.id === entry.id)) return;
    readRequest?.abort();
    const controller = new AbortController(), currentRevision = revision;
    readRequest = controller; selected = entry.id; preview = null; previewError = ''; reading = true;
    try {
      const result: WorkspaceFilePreview = await request(new URLSearchParams({conversation: id, snapshot, entry: entry.id}), controller);
      if (controller.signal.aborted || revision !== currentRevision) return;
      if (result?.path !== entry.path || result.bytes !== entry.bytes || typeof result.text !== 'string' || result.text.length > WORKSPACE_FILE_LIMITS.fileBytes || typeof result.sha256 !== 'string' || result.sha256.length !== 64 || !/^[a-f0-9]{64}$/.test(result.sha256)) {
        throw Error('The file preview was invalid. Refresh files and select again.');
      }
      preview = result;
    } catch (reason) {
      if (!controller.signal.aborted && revision === currentRevision) previewError = reason instanceof Error ? reason.message : 'This file cannot be previewed.';
    } finally {if (readRequest === controller && revision === currentRevision) reading = false;}
  }
  function bytes(value: number) {return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KiB`;}

  $effect(() => {
    const id = conversation;
    void refreshKey; // Parent changes this only when durable tool results arrive.
    untrack(reset);
    // Coalesce bursts of tool completions, not streaming tokens. No polling.
    autoRefresh = setTimeout(() => void refresh(id), 180);
    return () => {clearTimeout(autoRefresh); cancel();};
  });
</script>

<section class="workspace-files" aria-labelledby={heading} data-testid="workspace-files">
  <div class="workspace-heading">
    <div><h3 id={heading}>Workspace files</h3><p>Read-only · this conversation’s sandbox</p></div>
    <button type="button" class="workspace-refresh" onclick={() => void refresh()} disabled={loading || !workspaceConversation(conversation)} data-testid="workspace-refresh">{loading ? 'Refreshing…' : 'Refresh'}</button>
  </div>
  <p class="workspace-boundary">Text previews only. File names and contents are untrusted; nothing is executed.</p>
  <div class="workspace-status" role="status" data-testid="workspace-status">
    {#if !workspaceConversation(conversation)}Choose or start a conversation to inspect its files.
    {:else if loading}Checking this conversation’s workspace…
    {:else if listing?.state === 'missing'}No workspace yet. A sandboxed tool can create one; viewing does not.
    {:else if listing && !listing.entries.length}{listing.omitted || listing.truncated ? 'No safely previewable files in this bounded listing.' : 'This workspace is empty.'}
    {:else if listing}{listing.entries.length} file{listing.entries.length === 1 ? '' : 's'} · previews up to {bytes(WORKSPACE_FILE_LIMITS.fileBytes)}{/if}
  </div>
  {#if error}<p class="workspace-error" role="alert" data-testid="workspace-error">{error}</p>{/if}
  {#if listing?.truncated || listing?.omitted}
    <p class="workspace-boundary">Bounded listing: linked, special, oversized, internal or unsafe entries are omitted. Deep or large workspaces may be incomplete.</p>
  {/if}
  {#if listing?.entries.length}
    <ul class="workspace-list" aria-label="Listed workspace files">
      {#each listing.entries as entry (entry.id)}
        <li><button type="button" class="workspace-entry" aria-pressed={selected === entry.id} onclick={() => void select(entry)} data-testid="workspace-entry"><bdi>{entry.path}</bdi><span>{bytes(entry.bytes)}</span></button></li>
      {/each}
    </ul>
    {#if reading}<p class="workspace-status" role="status">Checking the listed file and reading its text…</p>{/if}
    {#if previewError}<p class="workspace-error" role="alert">{previewError}</p>{/if}
    {#if preview}
      <div class="workspace-preview-heading"><bdi>{preview.path}</bdi><span title={preview.sha256}>SHA-256 {preview.sha256.slice(0, 16)}…</span></div>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (The scrollable text preview needs keyboard scrolling.) -->
      <pre class="workspace-preview" tabindex="0" role="region" aria-label={`Text preview of ${preview.path}`} data-testid="workspace-preview">{preview.text || '(Empty file)'}</pre>
    {:else if !selected}<p class="workspace-boundary">Select a listed file to view its current text.</p>{/if}
  {/if}
</section>

<style>
  .workspace-files{min-width:0;color:var(--color-ink);font-size:var(--text-xs);line-height:1.6}
  .workspace-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .workspace-heading h3{font-size:var(--text-sm);font-weight:600;margin:0}
  .workspace-heading p,.workspace-boundary,.workspace-status{color:var(--color-muted);margin:5px 0 12px}
  .workspace-refresh{flex-shrink:0;min-height:36px;padding:5px 10px;border:1px solid var(--color-rule);border-radius:var(--radius-sm);background:var(--color-paper-2);font-size:var(--text-xs)}
  .workspace-list{list-style:none;margin:12px 0;padding:0;max-height:240px;overflow:auto;border-block:1px solid var(--color-rule)}
  .workspace-list li+li{border-top:1px solid var(--color-rule)}
  .workspace-entry{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:40px;padding:9px 8px;text-align:left;border:0;background:transparent;font:11px/1.5 var(--font-mono);white-space:normal}
  .workspace-entry bdi{min-width:0;overflow-wrap:anywhere}.workspace-entry span{flex-shrink:0;color:var(--color-muted);font-size:10px}
  .workspace-entry:hover,.workspace-entry[aria-pressed=true]{background:var(--color-paper-2)}
  .workspace-entry[aria-pressed=true]{box-shadow:inset 2px 0 var(--color-accent)}
  .workspace-error{margin:12px 0;padding-left:10px;border-left:2px solid var(--color-accent);overflow-wrap:anywhere;color:var(--color-ink)}
  .workspace-preview-heading{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px;margin:16px 0 8px;font:10px/1.5 var(--font-mono);overflow-wrap:anywhere}
  .workspace-preview-heading span{color:var(--color-muted)}
  .workspace-preview{max-height:360px;overflow:auto;margin:0;padding:12px;background:var(--color-paper-2);border:1px solid var(--color-rule);border-radius:var(--radius-sm);color:var(--color-ink);font:11px/1.6 var(--font-mono);white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2}
  @media(max-width:59.99rem){.workspace-refresh,.workspace-entry{min-height:44px}}
</style>
