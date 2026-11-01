<script lang="ts">
  import {Collapsible} from 'bits-ui';
  import {CaretDown} from 'phosphor-svelte';
  import type {AlertRules} from '$lib/pi/alerts';
  import type {Annotation} from '$lib/pi/annotation';
  let {conversation,request,turn,rules,available,saved,showPayload=true,onAnnotation}:{conversation:string;request:string;turn:number;rules:AlertRules;available:boolean;saved?:Annotation;showPayload?:boolean;onAnnotation?:(value:Annotation)=>void}=$props();
  let sending=$state(false),error=$state(''),received=$state<Annotation|undefined>();
  const annotation=$derived(saved?.status==='complete'||saved?.status==='failed'?saved:received??saved);
  async function ask(){
    if(sending||annotation||!available)return;sending=true;error='';
    try{const response=await fetch('/api/pi/annotate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({conversation,request,rules,consent:true})});
      const data=await response.json();if(!response.ok)throw Error(data.error??'Annotation unavailable.');received=data.annotation;onAnnotation?.(data.annotation);
    }catch(e){error=e instanceof Error?e.message:'Annotation unavailable.';}finally{sending=false;}
  }
</script>
<section class="meta-annotation" aria-label={`Muse Wonder second opinion for turn ${turn}`}>
  <h3>Muse Wonder <span>Second opinion · Turn {turn}</span></h3>
  <p class="meta-disclaimer">Muse Wonder's reading of these signals. A second model's interpretation, not a verdict or a lie detector.</p>
  {#if annotation}
    {#if annotation.status==='complete'}<p class="meta-note">{annotation.note}</p>
    {:else if annotation.status==='failed'}<p role="status">{annotation.error} This attempt is saved; it will not be retried automatically.</p>
    {:else}<p role="status">Request pending or interrupted. It will not be sent again automatically.</p>{/if}
    {#if annotation.diagnostic}<p class="meta-identity">Rejected by: {annotation.diagnostic.rule}{annotation.diagnostic.word_count!==null?` · ${annotation.diagnostic.word_count} words`:''}{annotation.diagnostic.character_count!==null?` · ${annotation.diagnostic.character_count} characters`:''}{annotation.diagnostic.finish_reason?` · finish ${annotation.diagnostic.finish_reason}`:''}{annotation.diagnostic.phrase_id!==null?` · phrase rule ${annotation.diagnostic.phrase_id}`:''}. Rejected prose is not retained.</p>{/if}
    <p class="meta-identity">{annotation.model} · {annotation.finished_at??annotation.started_at}</p>
    {#if showPayload}<Collapsible.Root><Collapsible.Trigger class="meta-payload-trigger">{annotation.status==='complete'?'View the exact payload sent':'View the prepared payload'}<CaretDown size={14}/></Collapsible.Trigger><Collapsible.Content>
      <p class="meta-hash">SHA-256 {annotation.payload_sha256}</p><pre>{annotation.payload_json}</pre>
    </Collapsible.Content></Collapsible.Root>{/if}
  {:else}
    <p class="meta-consent">Sends this turn's text and a summary of its readouts to Meta's API.</p>
    <p class="meta-detail">Reasoning is shortened when needed. Includes the recorded steering, prior-mean coverage and your current watch-list. Nothing is sent until you click.</p>
    <button onclick={ask} disabled={!available||sending}>{sending?'Asking Muse Wonder…':'Ask Muse Wonder to read these signals'}</button>
    {#if !available}<p role="status">Muse Wonder unavailable: the dedicated server key is missing or unreadable.</p>{/if}
  {/if}
  {#if error}<p role="alert">{error}</p>{/if}
</section>
<style>
  .meta-annotation{margin-top:18px;padding:16px;border:1px solid var(--color-rule);border-left:2px solid var(--color-accent);border-radius:7px;background:var(--color-paper-2);color:var(--color-ink-2)}
  .meta-annotation h3{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 9px;font-size:13px;font-weight:500}.meta-annotation h3 span{font:10px var(--font-mono);color:var(--color-muted)}
  .meta-annotation p{margin:8px 0;font-size:11px;line-height:1.7}.meta-annotation .meta-note{font-size:14px;color:var(--color-ink);white-space:pre-wrap}.meta-annotation .meta-disclaimer,.meta-annotation .meta-detail,.meta-annotation .meta-identity{color:var(--color-muted)}
  .meta-annotation button{border:1px solid var(--color-rule);background:var(--color-paper);color:var(--color-ink);border-radius:6px;padding:9px 12px;font-size:12px;text-align:left}.meta-annotation button:disabled{opacity:.6;cursor:not-allowed}
  .meta-annotation :global(.meta-payload-trigger){display:flex;align-items:center;gap:8px;margin-top:10px;font-size:11px}.meta-annotation .meta-hash{font:10px var(--font-mono);overflow-wrap:anywhere}.meta-annotation pre{font:10px/1.6 var(--font-mono);white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow-y:auto}
</style>
