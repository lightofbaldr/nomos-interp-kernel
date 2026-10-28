<script lang="ts">
  import type {LiveState} from '$lib/live';
  import {defaultRules} from '$lib/pi/alerts';
  import AlertTrail from './AlertTrail.svelte';
  let {live}:{live:LiveState}=$props();
  let enabled=$state(false),name=$state('afraid'),layer=$state(29),threshold=$state(5),side=$state<'above'|'below'>('above');
  let watch=$state(true),watchLayer=$state(44),prefixes=$state(defaultRules().workspace.prefixes.join('\n')),error=$state('');
  function apply(){error='';try{live.setAlertRules?.({emotion:{enabled,name,layer,threshold,side},workspace:{enabled:watch,layer:watchLayer,prefixes:prefixes.split('\n').filter(s=>s.trim())}});}catch(e){error=String(e);}}
  const names=$derived(live.assets?.directions[0]?.names??[]);
</script>
<div class="alert-controls">
  <h3>Readout rules, not verdicts</h3>
  <p>These badges describe internal signals. They do not classify lies, honesty, intent, feelings or safety. Defaults are illustrative rule choices, not validated thresholds.</p>
  <fieldset><legend>Emotion threshold</legend>
    <label class="check"><input type="checkbox" bind:checked={enabled}/>Enable projection crossing</label>
    <label>Direction <select aria-label="Alert emotion" bind:value={name}>{#each names as n}<option value={n}>{n}</option>{/each}</select></label>
    <label>Layer <input aria-label="Alert emotion layer" type="number" min="0" max="51" bind:value={layer}/></label>
    <label>Crossing <select aria-label="Alert crossing side" bind:value={side}><option value="above">Above prior mean</option><option value="below">Below prior mean</option></select></label>
    <label>Difference <input aria-label="Alert projection threshold" type="number" min="0.001" step="1" bind:value={threshold}/></label>
    <p>Raw projection minus the mean of all earlier defined reasoning positions in this conversation, at the same layer/direction asset. Warm-up: 32 earlier samples. The current sample is excluded from its baseline. No standard-deviation or significance claim.</p>
  </fieldset>
  <fieldset><legend>Workspace watch-list</legend>
    <label class="check"><input type="checkbox" bind:checked={watch}/>Enable reasoning watch-list</label>
    <label>Layer <input aria-label="Watch-list layer" type="number" min="0" max="51" bind:value={watchLayer}/></label>
    <label for="watch-prefixes">Token prefixes, one per line</label><textarea id="watch-prefixes" aria-label="Workspace watch-list" bind:value={prefixes} rows="5" maxlength="2080"></textarea>
    <p>Case-folded isolated token prefixes, with edge whitespace ignored. A badge means entry into the supplied top-k relative to the preceding known reasoning row—not a semantic match. Split words and unknown labels may be unobservable.</p>
  </fieldset>
  <button onclick={apply} disabled={!live.setAlertRules}>Apply alert rules</button>{#if error}<p role="alert">{error}</p>{/if}
  <p class="active">Active: emotion {live.alertRules?.emotion.enabled?`${live.alertRules.emotion.name} L${live.alertRules.emotion.layer}, ${live.alertRules.emotion.side} mean by ${live.alertRules.emotion.threshold}`:'off'}; watch-list {live.alertRules?.workspace.enabled?`L${live.alertRules.workspace.layer}`:'off'}.</p>
  <p>{live.alerts?.baselineSamples??0} defined baseline samples; {live.alerts?.unknownWorkspaceRows??0} reasoning rows with unavailable workspace or labels. Missing rows never become zeros. Gaps and turn boundaries reset crossing history. Settings are local to this page and rescan loaded history.</p>
  <AlertTrail {live}/>
</div>
<style>
  .alert-controls{padding:18px;font-size:12px;color:var(--color-ink-2)}.alert-controls h3{font-size:13px;font-weight:500}.alert-controls p{font-size:11px;color:var(--color-muted);line-height:1.7;margin:10px 0}.alert-controls fieldset{border:0;border-top:1px solid var(--color-rule);padding:12px 0;margin:14px 0}.alert-controls legend{font-weight:500;padding-right:8px}.alert-controls label{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:10px 0}.alert-controls .check{justify-content:flex-start}.alert-controls input[type=number]{width:80px}.alert-controls select{max-width:165px}.alert-controls input[type=number],.alert-controls select,.alert-controls textarea{border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink);padding:7px}.alert-controls textarea{width:100%;font:11px var(--font-mono);line-height:1.7;resize:vertical}.alert-controls button{padding:8px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink)}.alert-controls .active{color:var(--color-accent)}
</style>
