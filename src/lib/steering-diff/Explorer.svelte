<script lang="ts">
  import { onMount } from 'svelte';
  import { Tabs, Dialog } from 'bits-ui';
  import { Waveform, ArrowsSplit, ArrowLeft, ArrowRight, DownloadSimple, Database, CheckCircle, Info, X } from 'phosphor-svelte';
  import { catalog, loadIndex, loadBundle } from './data';
  import { armLabel, tokenText, scoreText, deltaText, channelJumps, relatedScenarios, freePrefill } from './model';
  import type { Bundle, BundleIndex, Dataset, Mode } from './model';
  import CandidatePanel from './CandidatePanel.svelte';
  import EmotionGrid from './EmotionGrid.svelte';
  import ResponseView from './ResponseView.svelte';
  import ScenarioContext from './ScenarioContext.svelte';
  import FullResponse from './FullResponse.svelte';
  let datasets = $state<{dataset:Dataset; index:BundleIndex}[]>([]);
  let activeDataset = $state(''); let prompt = $state('S1'); let arm = $state('joy_0.7'); let mode = $state<Mode>('matched');
  let sample=$state<number|null>(null);
  let bundle = $state<Bundle|null>(null); let error = $state(''); let loading = $state(true);
  let positionIndex = $state(0); let layer = $state(48); let selected = $state<number|null>(null); let query = $state('');
  const current = $derived(datasets.find(x=>x.dataset.id===activeDataset));
  const sampled=$derived(current?.index.comparisons.some(r=>r.prompt===prompt&&r.sample!==undefined)??false);
  const availableSamples=$derived([...new Set(current?.index.comparisons.filter(r=>r.prompt===prompt&&r.arm===arm&&r.mode===mode&&r.sample!==undefined).map(r=>r.sample!)??[])].sort((a,b)=>a-b));
  const selectedSample=$derived(availableSamples.length===1?availableSamples[0]:sample!==null&&availableSamples.includes(sample)?sample:null);
  const reference = $derived(current?.index.comparisons.find(r=>r.prompt===prompt && r.arm===arm && r.mode===mode && (r.sample??null)===(sampled?selectedSample:null)));
  const c = $derived(bundle?.comparison);
  const scenario = $derived(current?.index.scenarios?.[prompt] ?? null);
  const related = $derived(current ? relatedScenarios(current.index,scenario) : []);
  const recordedSource = $derived(scenario?.source ?? null);
  const prefill=$derived(freePrefill(scenario));
  const jumps = $derived(channelJumps(c?.positions ?? []));
  const selectedArm = $derived(current?.index.arms.find(a=>a.name===arm));
  const pos = $derived(c?.positions[positionIndex]);
  const workspace = $derived(pos?.workspace?.find(x=>x.layer===layer));
  const candidates = $derived((workspace?.candidates ?? []).filter(x => !query || (bundle?.vocab[x.id]??'').toLowerCase().includes(query.toLowerCase()) || String(x.id).includes(query)));
  const selectedCandidate = $derived(workspace?.candidates.find(x=>x.id===selected));
  const tokenWindow = $derived(c?.positions.slice(Math.max(0, positionIndex-8), positionIndex+14) ?? []);
  const onset = $derived(c?.steered.arm.steer?.from_pos ?? null);
  const availableArms = $derived(current?.index.arms.filter(a=>a.name!=='base'&&current.index.comparisons.some(r=>r.prompt===prompt&&r.arm===a.name)) ?? []);
  const sameSampleControls=$derived(sampled&&selectedSample!==null?availableArms.filter(a=>(a.kind==='afraid'||a.kind==='random1')&&current?.index.comparisons.some(r=>r.prompt===prompt&&r.arm===a.name&&r.mode===mode&&r.sample===selectedSample)):[]);
  const gates = $derived(Object.entries(current?.index.gates[prompt] ?? {}));
  const allGates = $derived(gates.length > 0 && gates.every(([,v])=>v.pass===true));
  const dataURL = $derived(current && reference ? `/data/${current.dataset.id}/${reference.path}` : '');
  onMount(async () => {
    try { const cat=await catalog(); const loaded=await Promise.all(cat.datasets.map(async dataset=>({dataset,index:await loadIndex(dataset)}))); datasets=loaded; activeDataset=loaded[0].dataset.id; prompt=Object.keys(loaded[0].index.prompts)[0]; if (!loaded[0].index.arms.some(a=>a.name===arm)) arm=''; }
    catch(e) {error=String(e); loading=false;}
  });
  $effect(() => {
    const d=current, r=reference;
    if (!d) return;
    const abort=new AbortController();
    bundle=null; selected=null; query=''; error='';
    if (!r) { loading=false; return; }
    loading=true;
    loadBundle(d.dataset,d.index,r,abort.signal).then(b=>{
      if (abort.signal.aborted) return;
      bundle=b; layer=b.comparison.settings.workspace?.layers.at(-1)??48;
      const firstAnswer=b.comparison.positions.findIndex(p=>p.channel==='answer');
      positionIndex=firstAnswer>=0?firstAnswer:b.comparison.positions.length-1;
      loading=false;
    }).catch(e=>{ if (!abort.signal.aborted) {error=String(e);loading=false;} });
    return ()=>abort.abort();
  });
  function changePrompt(v:string) { const [id,p]=v.split(':'); activeDataset=id;prompt=p;sample=null; if(!datasets.find(x=>x.dataset.id===id)?.index.comparisons.some(r=>r.prompt===p&&r.arm===arm)) arm=''; }
  function openRelated(key:string,targetMode:Mode) { prompt=key; mode=targetMode; }
  function jump(p:number) { const i=c?.positions.findIndex(x=>x.pos>=p) ?? -1; if(i>=0) positionIndex=i; }
</script>

<div class="steering-diff-root">
<a class="skip-link" href="#workspace">Skip to comparison</a>
<div class="lab-shell">
  <aside class="rail" aria-label="Glimmer Lab"><span class="brand-mark"><Waveform size={25} weight="bold"/></span><span class="rail-active" title="Workspace difference"><ArrowsSplit size={22}/></span><span class="rail-caption">LAB / 01</span><span class="rail-bottom mono">LOCAL</span></aside>
  <div class="main-shell">
    <header class="topbar"><div class="brand-name">Glimmer <span>Lab</span><span class="breadcrumb">/ Steering diff</span></div><div class="topbar-actions"><a href="/" class="chat-return"><ArrowLeft size={14}/>Back to chat</a><span class="recorded-pill"><span></span>Recorded, not live</span><Dialog.Root><Dialog.Trigger class="inline-flex min-h-10 items-center gap-2 rounded-md border border-[#30363e] px-3 text-xs text-[#bbc3d0]"><Database size={15}/>Source & gates</Dialog.Trigger><Dialog.Portal><div class="steering-diff-root steering-diff-portal"><Dialog.Overlay class="fixed inset-0 z-40 bg-black/75"/><Dialog.Content class="provenance-dialog fixed inset-y-4 right-4 z-50 w-[min(650px,calc(100vw-32px))] overflow-y-auto rounded-xl border border-[#30363e] bg-[#171c22] p-6 text-[#e7ebf1]"><div class="dialog-title-row"><Dialog.Title class="text-xl font-medium">Recorded evidence</Dialog.Title><Dialog.Close class="rounded-md p-2 text-[#bbc3d0]" aria-label="Close source details"><X size={20}/></Dialog.Close></div><Dialog.Description class="mt-2 mb-5 text-sm leading-6 text-[#aab2bf]">The browser verifies the copied file's SHA-256 against the local catalog. It renders the producer's comparison document; it does not run the model or derive the science.</Dialog.Description>
      {#if current}<h3 class="provenance-subtitle">Capture checks · {prompt}</h3><ul class="gate-list">{#each gates as [key,v]}<li><span>{key}</span><strong class:increase={v.pass===true}>{v.pass===true?'PASS':v.pass===false?'FAIL':'Unknown'}</strong>{#if v.detail}<small>{v.detail}</small>{/if}</li>{/each}</ul><h3 class="provenance-subtitle">Run & file identity</h3><dl class="identity-list"><dt>Run</dt><dd>{current.index.run}</dd><dt>Comparison SHA-256</dt><dd>{reference ? current.dataset.hashes[reference.path] : 'Unavailable'}</dd><dt>Schema</dt><dd>nomos.steerdiff.ui/1</dd><dt>Reference sequence</dt><dd>{c ? c.reference ?? 'Each arm generated independently' : 'No comparison selected'}</dd></dl><h3 class="provenance-subtitle">Scenario provenance</h3>{#if scenario}<pre class="identity-json">{JSON.stringify(scenario,null,2)}</pre>{:else}<p class="footnote">No scenario provenance supplied. No recorded-episode provenance is inferred.</p>{/if}<h3 class="provenance-subtitle">Engine, assets, tokenizer & schedule</h3><pre class="identity-json">{JSON.stringify(current.index.identity,null,2)}</pre><h3 class="provenance-subtitle">Per-arm identity & settings</h3><pre class="identity-json">{JSON.stringify(c ? {baseline:{run_id:c.baseline.run_id,arm:c.baseline.arm,identity:c.baseline.identity},steered:{run_id:c.steered.run_id,arm:c.steered.arm,identity:c.steered.identity},settings:c.settings} : null,null,2)}</pre>{/if}
      {#if bundle?.labels}<h3 class="provenance-subtitle">Supplied comparison labels</h3><pre class="identity-json">{JSON.stringify({sample:bundle.sample,source:current?.index.labels_source??null,labels:bundle.labels},null,2)}</pre><p class="footnote">Label source is producer-reported. The catalog binds the exported annotations; this viewer does not independently verify the source label file or re-label the answers.</p>{/if}
    </Dialog.Content></div></Dialog.Portal></Dialog.Root></div></header>
    <main>
      <section class="page-heading"><div><span class="eyebrow">INTERVENTION EXPLORER</span><h1>Workspace difference<span class="title-dot">.</span></h1><p>Hold the input fixed. Then look at what the model actually wrote.</p></div><a class="download-link" href={dataURL || undefined} aria-disabled={!dataURL} download><DownloadSimple size={16}/>Comparison JSON</a></section>
      <section class="experiment-controls" class:has-samples={sampled} aria-label="Experiment selection">
        <label class="prompt-select"><span class="control-label">SCENARIO</span><select aria-label="Scenario" value={`${activeDataset}:${prompt}`} onchange={e=>changePrompt(e.currentTarget.value)}>{#each datasets as d}<optgroup label={d.dataset.title??d.index.run}>{#each Object.entries(d.index.prompts) as [p,text]}<option value={`${d.dataset.id}:${p}`}>{d.dataset.title??p} · {text}</option>{/each}</optgroup>{/each}</select></label>
        <label><span class="control-label">INTERVENTION / CONTROL</span><select aria-label="Intervention" bind:value={arm}><option value="" disabled>Choose an intervention</option>{#each availableArms as a}<option value={a.name}>{armLabel(a)}</option>{/each}</select></label>
        {#if sampled}<label><span class="control-label">PAIRED SAMPLE</span><select aria-label="Paired sample" value={selectedSample??''} onchange={e=>sample=e.currentTarget.value===''?null:Number(e.currentTarget.value)} disabled={!availableSamples.length}><option value="" disabled>Choose sample</option>{#each availableSamples as k}<option value={k}>s{k} · both arms</option>{/each}</select></label>{/if}
        <div class="intervention-detail"><span class="control-label">INJECTION</span><strong class="mono">L24 <span>·</span> {!c?'not loaded':onset===null?'not armed':`from ${onset}`}</strong></div>
      </section>
      {#if current}{#key `${activeDataset}:${prompt}:${arm}:${mode}:${selectedSample}`}<FullResponse dataset={current.dataset} index={current.index} {prompt} {arm} {mode} sample={selectedSample}/>{/key}{/if}
      {#if current}<p class="prompt-copy"><span>{prompt}</span>{recordedSource?.question ?? current.index.prompts[prompt]}</p>{/if}
      {#if sampled}<section class="sample-context" aria-label="Sample pairing and interpretation"><strong>{reference?`Sample s${reference.sample} · baseline and steered at the same sample${bundle?` · paired seed ${bundle.comparison.baseline.arm.seed}`:''}`:'Choose an exported sample; no cross-sample comparison is inferred.'}</strong><p>Selected exploratory examples. Random pushes can change outcomes too; these examples do not establish an emotion-specific honesty mechanism or a lie detector. Sample numbers here are separate from the historical source episode’s sample.</p>{#if sameSampleControls.length>1}<div class="same-sample-controls"><span>Same-sample fear / random control:</span>{#each sameSampleControls as a}<button aria-pressed={a.name===arm} onclick={()=>arm=a.name}>{armLabel(a)}</button>{/each}</div>{/if}</section>{/if}
      {#if selectedArm?.s !== null && selectedArm?.s !== undefined && selectedArm.s < 0}<p class="signed-note">Counter-steered: negative strength reverses the <code>{selectedArm.kind}</code> direction. It is not a separately learned opposite emotion.</p>{/if}
      {#if scenario}<ScenarioContext {scenario} {related} onopen={openRelated}/>{/if}
      <Tabs.Root value={mode} onValueChange={v=>mode=v as Mode}>
        <div class="mode-row"><Tabs.List class="inline-flex flex-wrap gap-1 rounded-lg border border-[#30363e] bg-[#171c22] p-1" aria-label="Comparison mode"><Tabs.Trigger value="matched" class="mode-trigger">Matched input replay</Tabs.Trigger><Tabs.Trigger value="free" class="mode-trigger">Free generation</Tabs.Trigger></Tabs.List><span class="checks-indicator"><CheckCircle size={15}/>{allGates?'Recorded capture checks pass':'Check source record'}</span></div>
        <Tabs.Content value="matched"><div class="mode-explanation"><span class="mode-tag">{recordedSource?'RECORDED SEQUENCE':'SAME TOKENS'}</span><p>{scenario && !scenario.modes.includes('matched')?'Matched replay is not exported for this scenario. Open its explicitly linked replay scenario, if available.':recordedSource?'Both arms replay the exact recorded thinking tail and answer. Neither arm is generating a new reply in this mode.':'Both arms consume the baseline reply. Differences isolate the intervention on this fixed token history.'}</p></div></Tabs.Content>
        <Tabs.Content value="free"><div class="mode-explanation"><span class="mode-tag free">{prefill==='recorded-thinking'?'RECORDED THINKING → ANSWER':prefill==='prompt'?'PROMPT → OWN REASONING + ANSWER':'OWN TOKENS'}</span><p>{scenario && !scenario.modes.includes('free')?'Free generation is not exported for this scenario. Open its explicitly linked answer scenario, if available.':prefill==='recorded-thinking'?'The recorded thinking is prefilled; each arm then generates its own answer. This is not a replay of the recorded answer. Paired readouts stop at divergence.':prefill==='prompt'?'Only the prompt is prefilled; each arm generates its own reasoning and answer. Paired readouts stop before token divergence; later text is separate.':'Each arm writes its own reply. Only the shared history is compared; after divergence, the tracks separate.'}</p></div></Tabs.Content>
      {#if error}<div class="error-box" role="alert"><h2>Comparison unavailable</h2><p>{error}</p><p>No substitute values are displayed.</p></div>
      {:else if loading}<div class="loading-state" role="status"><span class="loading-dot"></span>Verifying the recorded bundle…</div>
      {:else if !reference}<div class="empty-state" role="status"><h2>{!arm?'Choose an intervention':sampled&&availableSamples.length&&selectedSample===null?'Choose a paired sample':`No ${mode==='matched'?'matched-input replay':'free-generation comparison'} for this arm`}</h2><p>Choose an arm, sample when available, and an exported mode for this scenario. The missing comparison is not treated as a zero effect; modes never switch automatically.</p></div>
      {:else if bundle && c && pos}
        <div class="view-navigation">{#if c.divergence}<span>Free histories split at <strong>{c.divergence.position}</strong>. Paired readouts stop at <strong>{c.divergence.position-1}</strong>.</span>{:else}<span>{mode==='matched'?(recordedSource?'Fixed recorded sequence · matched positions':'Fixed baseline sequence · matched positions'):'No divergence in this recorded comparison'}</span>{/if}<a href="#responses">{mode==='free'?'Read own continuations':'Read the fixed reference'} <span aria-hidden="true">↓</span></a></div>
        <section class="position-panel" aria-label="Consumed token timeline">
          <div class="position-heading"><div class="position-current"><span class="eyebrow">STATE AFTER CONSUMING</span><strong><code>{tokenText(bundle.vocab,pos.token)}</code><span class="mono">#{pos.token}</span></strong></div><div class="position-meta"><span class="channel-badge">{pos.source==='prompt'?(recordedSource?'Prefilled prefix':'Prompt'):pos.channel ?? 'Unlabelled'}</span><label class="mono">Position <input type="number" aria-label="Position" value={pos.pos} min={c.positions[0].pos} max={c.positions.at(-1)!.pos} oninput={e=>jump(Number(e.currentTarget.value))}/><span>/ {c.positions.at(-1)!.pos}</span></label></div></div>
          <div class="scrubber"><button aria-label="Previous token" disabled={positionIndex===0} onclick={()=>positionIndex--}><ArrowLeft size={16}/></button><input aria-label="Consumed position" type="range" min="0" max={c.positions.length-1} step="1" bind:value={positionIndex}/><button aria-label="Next token" disabled={positionIndex===c.positions.length-1} onclick={()=>positionIndex++}><ArrowRight size={16}/></button></div>
          <div class="timeline-labels"><button onclick={()=>positionIndex=0}>{recordedSource?'Prefill start':'Prompt start'}</button>{#if onset!==null}<button onclick={()=>jump(onset)}>L24 onset · {onset}</button>{/if}<button onclick={()=>jump(c.P)}>{mode==='matched'?'Stepped replay':'Generation'} · {c.P}</button>{#if c.divergence}<span class="split-label">Split at {c.divergence.position} · paired range ends here</span>{:else}<button onclick={()=>positionIndex=c.positions.length-1}>Recorded end</button>{/if}</div>
          <div class="channel-jumps"><span>Jump by channel</span><button disabled={jumps.reasoning===null} onclick={()=>jumps.reasoning!==null && jump(jumps.reasoning)}>{recordedSource && mode==='matched'?'Thinking tail':'Reasoning start'} {jumps.reasoning===null?'· unavailable':`· ${jumps.reasoning}`}</button><button disabled={jumps.answer===null} onclick={()=>jumps.answer!==null && jump(jumps.answer)}>Answer start {jumps.answer===null?'· unavailable':`· ${jumps.answer}`}</button><small>Supplied stepped-row labels only; no jump beyond the paired range.</small></div>
          <div class="token-ribbon" aria-label="Nearby consumed tokens">{#each tokenWindow as p}<button class:active={p.pos===pos.pos} class:prompt-token={p.source==='prompt'} onclick={()=>jump(p.pos)} title={`After consuming position ${p.pos}, token ${p.token}`} aria-label={`Position ${p.pos}: ${tokenText(bundle.vocab,p.token)}`} aria-pressed={p.pos===pos.pos}>{tokenText(bundle.vocab,p.token)}</button>{/each}</div>
        </section>
        <section id="workspace" class="workspace-section" aria-label="Workspace comparison">
          {#if c.settings.workspace}
          <div class="section-heading"><div><span class="eyebrow">APPROXIMATE J-LENS READOUT</span><h2>Baseline <span class="heading-divider">/</span> Steered <span class="heading-divider">/</span> Difference</h2></div><fieldset class="layer-picker"><legend class="sr-only">Workspace layer</legend>{#each c.settings.workspace.layers as l}<button aria-pressed={layer===l} onclick={()=>{layer=l;selected=null;}}>L{l}</button>{/each}</fieldset></div>
          <div class="workspace-toolbar"><span class="mono">L{layer} · display {c.display_k} / transmitted {c.settings.workspace.k}</span><label class="token-search"><span class="sr-only">Filter displayed candidates</span><input type="search" aria-label="Filter displayed candidates" placeholder="Find token or ID…" bind:value={query}/></label></div>
          {#if workspace && workspace.missing.length===0 && pos.missing.length===0}
            {#if !candidates.length}<div class="empty-state"><h3>No matching displayed candidates</h3><p>The search covers this layer's displayed candidate union, not the vocabulary.</p></div>{:else}<div class="comparison-grid">{#each ['baseline','steered','difference'] as side}<CandidatePanel side={side as 'baseline'|'steered'|'difference'} {candidates} vocab={bundle.vocab} displayK={c.display_k} transmittedK={c.settings.workspace.k} {selected} onselect={id=>selected=selected===id?null:id}/>{/each}</div>{/if}
          {:else}<div class="empty-state" data-testid="workspace-missing"><Info size={24}/><h3>{pos.source==='prompt'?'Prompt workspace is unavailable in this export':'Workspace comparison unavailable'}</h3><p>This is missing data, not zero change. {workspace?.missing.join(', ')} {pos.missing.join(', ')}</p></div>{/if}
          {#if selectedCandidate}<div class="candidate-detail" role="status"><code>{tokenText(bundle.vocab,selectedCandidate.id)}</code><span class="mono">#{selectedCandidate.id}</span><span>Baseline <strong>{scoreText(selectedCandidate,'baseline')}</strong></span><span>Steered <strong>{scoreText(selectedCandidate,'steered')}</strong></span><span>Δ <strong>{deltaText(selectedCandidate)}</strong></span><small>␠ space · ↵ newline · ⇥ tab. ≤ score means at most the supplied 16th-score bound.</small></div>{/if}
          <p class="footnote">Scores are interpreted J-lens readouts, not probabilities or proof of knowledge. “Entered” and “left” refer only to the displayed top {c.display_k}. Values are rounded for display; bounds round outward. Exact values are in the comparison JSON. Whitespace: ␠ space, ↵ newline, ⇥ tab.</p>
          {:else}<div class="empty-state" data-testid="workspace-disabled"><h2>Workspace was not captured</h2><p>This is an emotion-readout-only export. No workspace ranks, tokens or differences are available; absence is not zero change.</p></div>{/if}
        </section>
        <EmotionGrid position={pos} settings={c.settings} intervention={24}/>
        <ResponseView {bundle}/>
        <footer class="page-footer"><span><Database size={13}/>{current?.index.run} · {reference.path}</span><span>Selected illustration · no safety or topology verdict</span></footer>
      {/if}
      </Tabs.Root>
    </main>
  </div>
</div>

</div>
