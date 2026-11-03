<script lang="ts">
  import {Switch} from 'bits-ui';
  import {ArrowClockwise} from 'phosphor-svelte';
  import type {LiveState} from '$lib/live';
  import {generationSettings} from '$lib/pi/generation';
  let {live}:{live:LiveState}=$props();
  let error=$state('');
  const conversation=$derived(live.conversation),pi=$derived(live.pi);
  const decoding=$derived(generationSettings(pi?.entries??[]));
  $effect(()=>{conversation;error='';});
  async function sampling(value:boolean){
    const id=conversation;error='';
    try{await live.setSampling?.(value);}
    catch(e){if(id===conversation)error=e instanceof Error?e.message:'Could not save decoding.';}
  }
</script>

<section class="generation-controls" aria-label="Conversation and sampling settings">
  <div class="session-status"><span>{!conversation?'No conversation selected':pi?.busy?'Glimmer is generating':pi?.connected?'Durable Pi session':'Bridge disconnected'}</span>{#if conversation}<button onclick={()=>live.reconnect?.()} disabled={!live.reconnect} aria-label="Reconnect Pi bridge"><ArrowClockwise size={13} aria-hidden="true"/>Reconnect</button>{/if}</div>
  <div class="decoding-control">
    {#key `${conversation}:${decoding?.version}:${live.controlPending}`}<Switch.Root id="glimmer-sampling" class="sampling-switch" checked={decoding?.sampling??false} onCheckedChange={sampling} disabled={!pi?.connected||!decoding||pi.busy||live.controlPending||!live.setSampling} aria-label="Glimmer's sampling" aria-describedby="sampling-cost"><Switch.Thumb class="sampling-thumb"/></Switch.Root>{/key}
    <label for="glimmer-sampling">Glimmer's sampling <span>{decoding?.sampling?'On':'Off · greedy'}</span></label>
    <p id="sampling-cost">Slower: disables speculative decoding. Earlier Gold measurements: about 17 tokens/s sampled vs 40 greedy, without readouts. Actual speed varies; sampling can still repeat.</p>
  </div>
  {#if error}<p class="sampling-error" role="alert">{error}</p>{/if}
</section>

<style>
  .generation-controls{padding:18px;border-bottom:1px solid var(--color-rule);color:var(--color-ink-2)}
  .session-status{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px;font-size:11px;color:var(--color-muted)}
  .session-status button{display:flex;align-items:center;gap:6px;padding:7px 10px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink);font-size:11px}.session-status button:disabled{opacity:.5}
  .decoding-control{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:8px 10px;font-size:12px}.decoding-control label{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.decoding-control label span{color:var(--color-muted);font:10px var(--font-mono)}.decoding-control p{grid-column:2;margin:0;font-size:11px;line-height:1.7;color:var(--color-muted)}
  .decoding-control :global(.sampling-switch){display:flex;align-items:center;flex:none;width:34px;height:20px;padding:2px;background:var(--color-rule);border:1px solid var(--color-faint);border-radius:12px}.decoding-control :global(.sampling-switch[data-state=checked]){background:var(--color-accent)}.decoding-control :global(.sampling-switch:disabled){opacity:.55}.decoding-control :global(.sampling-thumb){display:block;width:14px;height:14px;border-radius:50%;background:var(--color-paper);transform:translateX(0)}.decoding-control :global(.sampling-thumb[data-state=checked]){transform:translateX(13px)}
  .sampling-error{font-size:11px;line-height:1.7;color:var(--color-ink)}
</style>
