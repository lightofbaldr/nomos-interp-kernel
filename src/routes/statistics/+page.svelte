<script lang="ts">
  import {page} from '$app/state';
  import {labContext} from '$lib/workspace/context';
  import InspectionSource from '$lib/components/InspectionSource.svelte';
  import {turnCounts,directionMeans,directionSeries,steeringText,trajectory,workspaceCoverage,type Channel} from '$lib/inspection/model';
  import {promptForTurn} from '$lib/workspace/model';
  import '$lib/inspection/pages.css';
  const context=labContext(),live=$derived(context.live),turns=$derived(live.pi?.turns??[]);
  const requested=$derived(page.url.searchParams.get('turn'));
  const turn=$derived(requested?turns.find(t=>t.request===requested):turns.at(-1));
  let requestedLayer=$state(29),channel=$state<Channel>('reasoning'),requestedName=$state('afraid');
  const layers=$derived(turn?.header?.settings?.emotion.layers??[]),layer=$derived(layers.includes(requestedLayer)?requestedLayer:layers.at(-1)??29);
  const names=$derived(turn?.header?.settings?.emotion.names??[]),name=$derived(names.includes(requestedName)?requestedName:names[0]??'');
  const counts=$derived(turn?turnCounts(turn):null),means=$derived(turn?directionMeans(turn,layer,channel):[]);
  const trace=$derived(trajectory(turn?directionSeries(turn,layer,name,channel):[]));
  const current=$derived(means.find(m=>m.name===name));
  const groups=$derived(turn?live.alerts?.groupCounts[turn.request]??0:0),entries=$derived(turn?live.alerts?.counts[turn.request]??0:0);
  const watch=$derived(turn&&live.alertRules?.workspace.enabled?workspaceCoverage(turn,live.alertRules.workspace.layer,live.labels??{}):null);
  const num=(v:number|null|undefined)=>v===null||v===undefined?'Unknown':v.toLocaleString('en-US');
  const mean=(v:number|null|undefined)=>v===null||v===undefined?'Not observed':`${v>=0?'+':''}${v.toFixed(2)}`;
</script>
<svelte:head><title>Statistics · Glimmer Lab</title><meta name="description" content="Saved turn counts, raw direction projections and readout coverage."/></svelte:head>
<div class="inspection-page statistics-page">
  <header class="page-head"><div><span class="eyebrow">MEASUREMENTS</span><h1>Statistics</h1><p class="lede">Counts and direction projections from saved turns, with missing observations kept visible.</p></div><span class="eyebrow">DESCRIPTIVE ONLY</span></header>
  <InspectionSource {live} {turn}/>
  {#if turn&&counts}
    <p class="prompt">{promptForTurn(live.pi!,turn)||'Waiting for the saved prompt entry…'}</p>
    {#if turn.status!=='complete'}<p class="notice">This turn is {turn.status.replaceAll('_',' ')}. Counts and means describe only the records received so far.</p>{/if}
    <div class="metric-row">
      <div class="metric"><span>Reasoning positions</span><strong>{num(counts.reasoning)}</strong><small>Generated, observed rows</small></div>
      <div class="metric"><span>Answer positions</span><strong>{num(counts.answer)}</strong><small>Generated, observed rows</small></div>
      <div class="metric"><span>Workspace coverage</span><strong>{turn.header?.settings?.workspace?`${counts.workspace.toLocaleString()}`:'Off'}</strong><small>{turn.header?.settings?.workspace?`of ${counts.rows.toLocaleString()} observed positions`:'Not captured for this turn'}</small></div>
      <div class="metric"><span>Observed signal alerts</span><strong>{live.alerts?num(groups):'Unknown'}</strong><small>{live.alerts?`${num(entries)} entries · current rules`:'Alert replay not available'}{#if watch?.unknown}<br/>{num(watch.unknown)} reasoning rows unknown to watch-list{/if}</small></div>
    </div>
    <div class="two-columns">
      <section class="panel"><header><h2>Direction over the turn</h2><span>RAW PROJECTION · NOT A DIFFERENCE</span></header>
        <div class="chart-controls"><label>Channel <select aria-label="Statistics channel" bind:value={channel}><option value="reasoning">Reasoning</option><option value="answer">Answer</option></select></label><label>Layer <select aria-label="Statistics layer" value={layer} onchange={e=>requestedLayer=Number(e.currentTarget.value)} disabled={!layers.length}>{#each layers as l}<option value={l}>L{l}</option>{/each}</select></label><label>Direction <select aria-label="Statistics direction" value={name} onchange={e=>requestedName=e.currentTarget.value} disabled={!names.length}>{#each names as n}<option>{n}</option>{/each}</select></label></div>
        {#if trace.defined}
          <div class="trace-summary"><strong>{mean(current?.mean)}</strong><span>{name} · L{layer} · {channel} mean<br/>{trace.defined.toLocaleString()} defined / {trace.total.toLocaleString()} observed rows</span></div>
          <svg class="trajectory" viewBox="0 0 800 220" role="img" aria-label={`${name} at layer ${layer}, raw projection over consumed positions. Gaps are not interpolated.`}><title>{name}: {mean(current?.mean)} mean, {trace.total-trace.defined} missing rows</title><line x1="24" y1="105" x2="776" y2="105" class="zero"/>{#each trace.paths as d}<path {d}/>{/each}<text x="24" y="14">+{trace.extent.toFixed(1)}</text><text x="24" y="198">−{trace.extent.toFixed(1)}</text><text x="24" y="217">Position {trace.first}</text><text x="776" y="217" text-anchor="end">{trace.last}</text></svg>
          <p class="muted">Each point follows a consumed token. Missing rows and position gaps break the line. The vertical scale follows this selection.</p>
        {:else}<p class="notice">{names.length?`No defined ${name} readouts in this channel at L${layer}.`:'No emotion readouts were captured for this turn.'} Unknown is not zero.</p>{/if}
      </section>
      <section class="panel"><header><h2>What this turn captured</h2></header><dl>
        <div><dt>Recorded steering</dt><dd>{steeringText(turn)}</dd></div>
        <div><dt>Emotion rows</dt><dd class="number">{num(counts.emotion)} / {num(counts.rows)}</dd></div>
        <div><dt>Prompt rows observed</dt><dd class="number">{num(counts.prompt)}</dd></div>
        <div><dt>Reused prefix positions</dt><dd class="number">{num(counts.reused)}</dd></div>
        <div><dt>Template / tool / other rows</dt><dd class="number">{num(counts.other)}</dd></div>
        <div><dt>Readout state</dt><dd>{turn.status.replaceAll('_',' ')}</dd></div>
      </dl><p class="muted">Reused positions are not fresh observations. Observed positions are not the model's full output-token count.</p><p><a href={`/workspace?conversation=${live.conversation}&turn=${encodeURIComponent(turn.request)}`}>Inspect this turn’s workspace →</a></p></section>
    </div>
    <section class="panel"><header><h2>Direction means</h2><span>L{layer} · {channel} · largest absolute means first</span></header><p class="muted">All captured directions, averaged over defined rows only. Alignment with a direction is not a feeling or a calibrated probability.</p>
      <div class="table-scroll"><table><thead><tr><th>Direction</th><th>Raw mean</th><th>Defined</th><th>Missing</th></tr></thead><tbody>{#each means as m}<tr><td>{m.name}</td><td class="number">{mean(m.mean)}</td><td class="number">{m.defined}</td><td class="number">{m.missing}</td></tr>{/each}</tbody></table></div>
    </section>
    <section class="panel"><header><h2>This conversation</h2><span>{turns.length} recorded model turns · not pooled across settings</span></header><div class="table-scroll"><table><thead><tr><th>Turn</th><th>State</th><th>Reasoning rows</th><th>Answer rows</th><th>Recorded steering</th></tr></thead><tbody>{#each turns as t}{@const c=turnCounts(t)}<tr><td><a href={`?conversation=${live.conversation}&turn=${encodeURIComponent(t.request)}`}>{t.turn}</a></td><td>{t.status.replaceAll('_',' ')}</td><td class="number">{num(c.reasoning)}</td><td class="number">{num(c.answer)}</td><td>{steeringText(t)}</td></tr>{/each}</tbody></table></div></section>
    <p class="page-note">No causal steering effect, confidence score or lie rate is estimated here. Alert counts replay the current rules over saved reasoning rows; they are not verdicts. Their baseline is the running mean of earlier defined reasoning rows in this conversation, including this turn, after a 32-row warm-up; answers never update it.</p>
  {:else}<div class="empty"><h2>{requested?'Turn not found':'No turn to measure yet'}</h2><p>Choose a conversation with saved readouts, or send a message with emotion capture enabled. These numbers do not require workspace capture.</p><a href="/">Back to chat →</a></div>{/if}
</div>
<style>
  .chart-controls{display:flex;flex-wrap:wrap;gap:12px;margin:17px 0}.chart-controls select{max-width:160px}.trace-summary{display:flex;gap:18px;align-items:center;margin:24px 0 12px}.trace-summary strong{font:34px var(--font-mono);letter-spacing:-1.5px;color:var(--color-accent)}.trace-summary span{font-size:11px;color:var(--color-muted);line-height:1.8}.trajectory{width:100%;display:block;margin-bottom:12px;overflow:visible}.trajectory path{fill:none;stroke:var(--color-accent);stroke-width:1.6;stroke-linecap:round}.trajectory .zero{stroke:var(--color-rule);stroke-dasharray:4 5}.trajectory text{fill:var(--color-muted);font:11px var(--font-mono)}
</style>
