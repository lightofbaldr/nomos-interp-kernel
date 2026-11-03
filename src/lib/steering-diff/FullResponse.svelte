<script lang="ts">
  import { Dialog } from 'bits-ui';
  import { ArrowUpRight, X } from 'phosphor-svelte';
  import { loadBundle } from './data';
  import { armLabel, fullResponseTarget } from './model';
  import type { Bundle, BundleIndex, Dataset, Mode } from './model';
  import ResponseView from './ResponseView.svelte';

  let {dataset,index,prompt,arm,mode,sample}:{dataset:Dataset;index:BundleIndex;prompt:string;arm:string;mode:Mode;sample:number|null}=$props();
  let open=$state(false),loading=$state(false),error=$state(''),response=$state<Bundle|null>(null);
  const target=$derived(fullResponseTarget(index,prompt,arm,sample,mode));
  const selectedArm=$derived(index.arms.find(a=>a.name===arm));
  const responseURL=$derived(target.ref?`/data/${dataset.id}/${target.ref.path}`:'');

  $effect(()=>{
    if(!open || !target.ref)return;
    const ref=target.ref,abort=new AbortController();
    response=null;error='';loading=true;
    // Always read this dataset's pinned file on explicit opening. Do not borrow
    // an in-flight readout bundle or cached text from a previous selection.
    loadBundle(dataset,index,ref,abort.signal).then(b=>{
      if(!abort.signal.aborted){response=b;loading=false;}
    }).catch(e=>{
      if(!abort.signal.aborted){error=String(e);loading=false;}
    });
    return()=>abort.abort();
  });
</script>

<div class="full-response-access">
  <Dialog.Root bind:open>
    <Dialog.Trigger class="full-response-trigger" disabled={!target.ref}>Full steered response <ArrowUpRight size={16} aria-hidden="true"/></Dialog.Trigger>
    <Dialog.Portal><div class="steering-diff-root steering-diff-portal">
      <Dialog.Overlay class="fixed inset-0 z-40 bg-black/75"/>
      <Dialog.Content class="full-response-dialog fixed inset-y-4 left-1/2 z-50 w-[min(1100px,calc(100vw-32px))] -translate-x-1/2 overflow-y-auto rounded-xl border border-[#30363e] bg-[#171c22] p-4 text-[#e7ebf1] sm:p-6">
        <div class="dialog-title-row"><div><span class="eyebrow">RECORDED FREE GENERATION · NOT LIVE</span><Dialog.Title class="mt-2 text-xl font-medium">Full steered response</Dialog.Title></div><Dialog.Close class="shrink-0 rounded-md p-2 text-[#bbc3d0]" aria-label="Close full response"><X size={20}/></Dialog.Close></div>
        <Dialog.Description class="mt-3 text-sm leading-6 text-[#aab2bf]">{selectedArm?armLabel(selectedArm):arm}{sample===null?'':` · sample s${sample}`} · {target.ref?.prompt}. {mode==='matched'?'The workspace behind this dialog remains a fixed-input replay. This text comes from a separate free-generation capture; it is not what that replay generated.':'The complete exported reply is shown here, including text after the readout histories diverge.'}</Dialog.Description>
        {#if target.relation==='same-source'}<p class="response-origin-note">Linked by the same source episode’s path and SHA-256. The replay and answer scenarios use different prefixes; their positions are not aligned.</p>{/if}
        {#if error}<div class="error-box" role="alert"><h2>Response unavailable</h2><p>{error}</p><p>No reference text or other response is substituted.</p></div>
        {:else if loading}<p class="loading-state" role="status">Verifying the recorded response…</p>
        {:else if response}<ResponseView bundle={response} id="full-responses" steeredFirst/>
          <details class="response-identity"><summary>Response source & identity</summary><dl class="identity-list"><dt>Run</dt><dd>{index.run}</dd><dt>Scenario</dt><dd>{response.prompt}</dd><dt>Steered run</dt><dd>{response.comparison.steered.run_id}</dd><dt>File</dt><dd>{target.ref?.path}</dd><dt>SHA-256</dt><dd>{target.ref?dataset.hashes[target.ref.path]:''}</dd></dl><a href={responseURL} download>Download this response comparison JSON</a></details>
        {/if}
      </Dialog.Content>
    </div></Dialog.Portal>
  </Dialog.Root>
  <p>{target.reason??(mode==='matched'?'Read the selected arm’s separately generated reply. The fixed workspace replay stays in place.':'Read this arm’s full saved reply, not just the selected token or shared prefix.')}</p>
</div>
