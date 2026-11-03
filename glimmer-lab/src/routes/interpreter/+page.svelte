<script lang="ts">
  import {page} from '$app/state';
  import {Collapsible,Tabs} from 'bits-ui';
  import {labContext} from '$lib/workspace/context';
  import InspectionSource from '$lib/components/InspectionSource.svelte';
  import MetaAnnotation from '$lib/components/MetaAnnotation.svelte';
  import {annotationFor,type Annotation} from '$lib/pi/annotation';
  import {defaultRules} from '$lib/pi/alerts';
  import {promptForTurn} from '$lib/workspace/model';
  import {payloadReading} from '$lib/inspection/payload';
  import '$lib/inspection/pages.css';
  const context=labContext(),live=$derived(context.live),turns=$derived(live.pi?.turns??[]),requested=$derived(page.url.searchParams.get('turn'));
  const turn=$derived(requested?turns.find(t=>t.request===requested):turns.at(-1));
  let received=$state<Record<string,Annotation>>({});
  const key=$derived(`${live.conversation}:${turn?.request}`);
  const saved=$derived(turn?annotationFor(live.pi?.entries??[],turn.request):undefined);
  const annotation=$derived(saved?.status==='complete'||saved?.status==='failed'?saved:received[key]??saved);
  const reading=$derived(payloadReading(annotation));
  let payloadTab=$state('summary'),desiredLayer=$state(29),channel=$state('reasoning');
  const selectedLayer=$derived(reading?.layers.find(l=>l.layer===desiredLayer)??reading?.layers.at(-1));
  const selectedChannel=$derived(selectedLayer?.channels.find(c=>c.channel===channel));
  const value=(n:number|null)=>n===null?'Not supplied':`${n>=0?'+':''}${n.toFixed(2)}`;
  function receiveFor(target:string){return (a:Annotation)=>{received={...received,[target]:a};};}
</script>
<svelte:head><title>Interpreter · Glimmer Lab</title><meta name="description" content="Muse Wonder's optional second opinion beside the exact signal payload it received."/></svelte:head>
<div class="inspection-page interpreter-page">
  <header class="page-head"><div><span class="eyebrow">A SECOND READING</span><h1>Interpreter</h1><p class="lede">Muse Wonder’s optional reading of a finished turn, beside the exact evidence prepared for it.</p></div><span class="eyebrow">INTERPRETATION ≠ MEASUREMENT</span></header>
  <InspectionSource {live} {turn}/>
  {#if turn}
    <p class="prompt">{promptForTurn(live.pi!,turn)}</p>
    <p class="notice">This note is saved outside Glimmer’s model context. It never becomes an instruction or evidence in a later Glimmer turn.</p>
    <div class="interpret-columns">
      <section class="panel evidence-pane" aria-label="Interpreter input"><header><h2>{annotation?'Prepared signal payload':'What Muse Wonder receives'}</h2><span>{annotation?'SAVED BY THE SERVER':'NOT SENT'}</span></header>
        {#if annotation}
          <p class="muted">{annotation.status==='complete'?'The exact payload for this completed annotation.':'The exact prepared payload for this attempt; its presence alone does not prove delivery.'} Includes text, recorded steering, raw means, prior-mean differences and workspace observations.</p>
          <p class="payload-hash">SHA-256 {annotation.payload_sha256}</p>
          <Tabs.Root bind:value={payloadTab}><Tabs.List class="payload-tabs" aria-label="Payload display"><Tabs.Trigger value="summary">Signal summary</Tabs.Trigger><Tabs.Trigger value="request">Exact request</Tabs.Trigger></Tabs.List>
            <Tabs.Content value="summary">
              {#if reading}
                <dl><div><dt>Recorded steering</dt><dd>{reading.steering}</dd></div><div><dt>Reasoning / answer characters</dt><dd class="number">{reading.reasoning.characters??'Unknown'} / {reading.answer.characters??'Unknown'}</dd></div></dl>
                <div class="payload-select"><label>Layer <select aria-label="Payload layer" value={selectedLayer?.layer??''} onchange={e=>desiredLayer=Number(e.currentTarget.value)}>{#each reading.layers as l}<option value={l.layer}>L{l.layer}</option>{/each}</select></label><label>Channel <select aria-label="Payload channel" bind:value={channel}><option value="reasoning">Reasoning</option><option value="answer">Answer</option></select></label></div>
                <p class="muted">Values already present in the payload. Raw projection is not a difference; the prior-mean difference is not a steering effect.</p>
                <div class="table-scroll"><table><thead><tr><th>Direction</th><th>Raw mean</th><th>Prior-mean Δ</th><th>Defined<br/>raw / Δ</th></tr></thead><tbody>{#each selectedChannel?.rows??[] as r}<tr><td>{r.name}</td><td class="number">{value(r.raw)}</td><td class="number">{value(r.delta)}</td><td class="number">{r.defined??'—'} / {r.differenceDefined??'—'}</td></tr>{/each}</tbody></table></div>
                <p class="muted">{selectedChannel?.total??'Unknown'} channel rows · {selectedChannel?.status?.replaceAll('_',' ')??'No channel summary'}. Only the payload’s selected directions are shown; an omitted direction is not zero.</p>
                <Collapsible.Root><Collapsible.Trigger class="action">Text excerpts included in the payload</Collapsible.Trigger><Collapsible.Content><div class="payload-text">{#each ['prompt','reasoning','answer'] as kind}{@const excerpt=reading[kind as 'prompt'|'reasoning'|'answer']}<h3>{kind} {excerpt.truncated?'· shortened':''}</h3><pre>{excerpt.text??'Not supplied'}</pre>{/each}</div></Collapsible.Content></Collapsible.Root>
              {:else}<p class="notice">This payload has no recognized signal-summary view. Its exact request remains available in the next tab.</p>{/if}
            </Tabs.Content>
            <Tabs.Content value="request"><pre class="payload" data-testid="interpreter-payload">{annotation.payload_json}</pre></Tabs.Content>
          </Tabs.Root>
        {:else}
          <ol class="input-list"><li><span>01</span><div><h3>The turn’s text</h3><p>The prompt, reasoning and answer. Long text is shortened with truncation explicitly marked.</p></div></li><li><span>02</span><div><h3>Measured signals</h3><p>Raw direction means, differences from the running reasoning baseline, coverage and workspace top-k observations.</p></div></li><li><span>03</span><div><h3>The recorded conditions</h3><p>This turn’s steering and readout settings—not the Controls form’s current values.</p></div></li></ol>
          <p class="muted">There is no saved payload yet. Only an explicit request sends data to Meta’s API; simply opening this page does not.</p>
        {/if}
      </section>
      <section class="panel interpretation-pane" aria-label="Interpreter note"><header><h2>Muse Wonder’s reading</h2><span>{annotation?.status??'OPT-IN'}</span></header>
        {#if turn.status==='complete'&&live.conversation}
          {#key key}<MetaAnnotation conversation={live.conversation} request={turn.request} turn={turn.turn} rules={live.alertRules??defaultRules()} available={live.metaAvailable===true} saved={annotation} showPayload={false} onAnnotation={receiveFor(key)}/>{/key}
        {:else}<p class="notice">Only a completed, saved turn can be sent for interpretation. This turn is {turn.status.replaceAll('_',' ')}.</p>{/if}
        <div class="reading-limits"><h3>Read this as a second opinion</h3><p>Not a lie detector, a thought transcript, or proof that Glimmer knew an answer. Direction alignment is not a feeling.</p><p>The alert baseline includes earlier steered rows. A difference from that baseline is not an estimate of the steering effect.</p><p>Undefined values and warm-up coverage remain explicit in the payload. No missing measurement is replaced with zero.</p></div>
        <Collapsible.Root><Collapsible.Trigger class="action">How the comparison is defined</Collapsible.Trigger><Collapsible.Content><div class="definition"><p>The baseline is a running mean of earlier defined reasoning rows in this conversation, including the current turn, with a 32-row warm-up; answer rows never update it.</p><p>Raw projection means are separate from those differences. They include defined warm-up rows and do not imply comparison with an unsteered run.</p></div></Collapsible.Content></Collapsible.Root>
        <p class="page-note"><a href={`/statistics?conversation=${live.conversation}&turn=${encodeURIComponent(turn.request)}`}>Read the measurements without an interpretation →</a></p>
      </section>
    </div>
  {:else}<div class="empty"><h2>{requested?'Turn not found':'Choose a finished turn'}</h2><p>Your saved conversations are on the left. Once a turn is complete, you can inspect its signals or explicitly ask Muse Wonder for a second opinion.</p><a href="/">Back to chat →</a></div>{/if}
</div>
<style>
  .interpret-columns{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:30px;margin-top:26px}.input-list{list-style:none;margin:22px 0 26px;padding:0}.input-list li{display:flex;gap:18px;padding:18px 0;border-bottom:1px solid var(--color-rule)}.input-list li>span{font:11px var(--font-mono);color:var(--color-accent)}.input-list h3{margin-bottom:7px}.input-list p{color:var(--color-muted);max-width:52ch}.payload-hash{font:10px/1.8 var(--font-mono);overflow-wrap:anywhere;color:var(--color-muted);padding:14px 0}.payload{background:var(--color-paper-2);border:1px solid var(--color-rule);border-radius:7px;padding:18px;max-height:60vh;overflow:auto;tab-size:2}.reading-limits{padding:22px 0;color:var(--color-muted)}.reading-limits h3{color:var(--color-ink);margin-bottom:10px}.reading-limits p{margin:10px 0;max-width:55ch}.definition{padding-top:14px;color:var(--color-muted)}.interpretation-pane :global(.meta-annotation){margin-top:0;padding:18px}.interpretation-pane :global(.meta-note){font-size:16px;line-height:1.9}.interpretation-pane :global(.meta-annotation h3){display:none}
  @media(max-width:1000px){.interpret-columns{grid-template-columns:1fr}.interpretation-pane{grid-row:1}.payload{max-height:500px}}
  .evidence-pane :global(.payload-tabs){display:flex;gap:20px;border-bottom:1px solid var(--color-rule);margin-bottom:14px}.evidence-pane :global(.payload-tabs button){padding:10px 0;background:none;border:0;border-bottom:2px solid transparent;font-size:12px;color:var(--color-muted)}.evidence-pane :global(.payload-tabs button[data-state=active]){border-color:var(--color-accent);color:var(--color-ink)}.payload-select{display:flex;gap:16px;margin:18px 0}.payload-text{padding-top:18px}.payload-text h3{text-transform:capitalize;margin:14px 0 8px}.payload-text pre{max-height:300px;overflow:auto;color:var(--color-muted)}
</style>
