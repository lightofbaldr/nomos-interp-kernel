<script lang="ts">
  import {page} from '$app/state';
  import {goto} from '$app/navigation';
  import type {LiveState} from '$lib/live';
  import type {TurnView} from '$lib/pi/model';
  import {generationFor,generationLabel} from '$lib/pi/generation';
  let {live,turn}:{live:LiveState;turn:TurnView|undefined}=$props();
  function select(request:string){const u=new URL(page.url);u.searchParams.set('turn',request);void goto(u.pathname+u.search,{noScroll:true,keepFocus:true});}
</script>
<div class="inspection-source">
  <label>Conversation <select aria-label="Inspection conversation" value={live.conversation??''} onchange={e=>live.openConversation?.(e.currentTarget.value)}>
    {#if !live.conversation}<option value="">Choose from history</option>{/if}
    {#each live.conversations??[] as c}<option value={c.id}>{c.title??`Conversation ${c.id}`}</option>{/each}
  </select></label>
  <label>Turn <select aria-label="Inspection turn" value={turn?.request??''} onchange={e=>select(e.currentTarget.value)} disabled={!live.pi?.turns.length}>
    {#if !live.pi?.turns.length}<option value="">No captured turns</option>{/if}
    {#each live.pi?.turns??[] as t}<option value={t.request}>{t.turn} · {t.status.replaceAll('_',' ')}</option>{/each}
  </select></label>
  <span class="source-status">{live.pi?.connected?'Saved Pi records':live.conversation?'Connecting to saved records…':'No conversation selected'}</span>
  {#if turn}<span data-testid="inspection-decoding">{generationLabel(generationFor(live.pi?.entries??[],turn.task,turn.request))}</span>{/if}
</div>
