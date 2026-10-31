<script lang="ts">
  import * as Tool from '@nomos/svelte-ai-elements/tool';
  import type {Message} from '@earendil-works/pi-ai';
  import type {EntryRecord} from 'glimmer-agent/pi';
  import {textOf} from '$lib/pi/model';
  let {message,entries,streaming}:{message:Message;entries:EntryRecord[];streaming:boolean}=$props();
  const calls=$derived(message.role==='assistant'?message.content.filter(b=>b.type==='toolCall'):[]);
  const results=$derived(entries.flatMap(e=>e.model??[]).filter(m=>m.role==='toolResult'));
</script>
{#each calls as call (call.id)}
  {@const result=results.find(r=>r.toolCallId===call.id)}
  <div class="file-tool" data-testid="file-tool-call">
    <Tool.Root><Tool.Header type={call.name} state={result?result.isError?'output-error':'output-available':streaming?'input-streaming':'input-available'}/><Tool.Content>
      <p>Recorded Pi tool call. File tools are confined to this conversation's sandboxed /work.</p>
      <pre aria-label={`${call.name} arguments`}>{JSON.stringify(call.arguments,null,2)}</pre>
      {#if result}<pre aria-label={`${call.name} result`}>{textOf(result)}</pre>{:else}<p>Awaiting a durable result. This is not a completion claim.</p>{/if}
    </Tool.Content></Tool.Root>
  </div>
{/each}
<style>
  .file-tool{margin:12px 0;color:var(--color-ink);overflow:hidden}.file-tool p{font-size:11px;color:var(--color-muted);margin:8px 12px}.file-tool pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto;font:11px/1.6 var(--font-mono);padding:12px;background:var(--color-paper-2)}
</style>
