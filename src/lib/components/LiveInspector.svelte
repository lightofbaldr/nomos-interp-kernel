<script lang="ts">
  import {Tabs} from 'bits-ui';
  import type {LiveState} from '$lib/live';
  import {emptyPi,selectedRow,projection} from '$lib/pi/model';
  import AlertControls from './AlertControls.svelte';
  import AgentProfile from './AgentProfile.svelte';
  let {live}:{live:LiveState}=$props();
  let tab=$state('signals'),emotionLayer=$state(29),workspaceLayer=$state(44),workspace=$state(false);
  let direction=$state('afraid'),steerLayer=$state(24),strength=$state(0),pending=$state(false),error=$state('');
  const pi=$derived(live.pi??emptyPi()),chosen=$derived(selectedRow(pi,live.selection));
  const turn=$derived(chosen?.turn??pi.turns.at(-1)),rows=$derived(turn?.records??[]);
  const index=$derived(chosen?rows.findIndex(r=>r.pos===chosen.record.pos):-1);
  const d=$derived(live.assets?.directions[0]),lens=$derived(live.assets?.lenses[0]);
  const w=$derived(chosen?.turn.header?.settings?.workspace);
  const wLayer=$derived(w?.layers.includes(workspaceLayer)?workspaceLayer:w?.layers[0]);
  const top=$derived(wLayer===undefined?null:chosen?.record.workspace?.top[w?.layers.indexOf(wLayer)??-1]??null);
  const e=$derived(chosen?.turn.header?.settings?.emotion);
  const eLayer=$derived(e?.layers.includes(live.emotionLayer??29)?live.emotionLayer??29:e?.layers.at(-1));
  // Hydrate once per selected session, not on every streamed row/settings event.
  // A disconnect/reconnect of the same session must preserve an unsaved draft.
  let formSession:string|undefined,hydrated=false;
  $effect(()=>{
    const id=live.conversation;
    if(id!==formSession){formSession=id;hydrated=false;emotionLayer=29;workspaceLayer=44;workspace=false;direction='afraid';steerLayer=24;strength=0;error='';}
    if(hydrated||!id||!pi.connected||!pi.settings)return;
    const saved=pi.settings,readout=saved.readout,el=readout.emotion?.layers??[],wl=readout.workspace?.layers??[];
    emotionLayer=el.includes(29)?29:el[0]??(d?.layers.includes(29)?29:d?.layers[0]??29);
    workspace=readout.enabled&&Boolean(readout.workspace);workspaceLayer=wl.includes(44)?44:wl[0]??(lens?.layers.includes(44)?44:lens?.layers[0]??44);
    direction=saved.steer?.direction??(d?.names.includes('afraid')?'afraid':d?.names[0]??'afraid');
    steerLayer=saved.steer?.layer??(d?.layers.includes(24)?24:d?.layers[0]??24);strength=saved.steer?.strength??0;hydrated=true;
  });
  async function configure(action:'readout'|'steer',value:unknown){pending=true;error='';try{await live.command?.(action,value);}catch(e){error=String(e);}finally{pending=false;}}
  function readouts(){if(d)void configure('readout',{enabled:true,emotion:{directions:d.id,layers:[emotionLayer]},workspace:workspace&&lens?{lens:lens.id,layers:[workspaceLayer],k:16}:null});}
  function steer(){if(d)void configure('steer',{directions:d.id,layer:steerLayer,direction,strength});}
  function rowAt(i:number){if(turn&&rows[i])live.select?.({request:turn.request,pos:rows[i].pos});}
</script>
<div class="live-inspector">
  <Tabs.Root bind:value={tab}>
    <Tabs.List class="tab-list" aria-label="Live inspection views"><Tabs.Trigger value="signals">Signals</Tabs.Trigger><Tabs.Trigger value="control">Controls</Tabs.Trigger><Tabs.Trigger value="profile">Profile</Tabs.Trigger><Tabs.Trigger value="alerts">Alerts</Tabs.Trigger><Tabs.Trigger value="status">Status</Tabs.Trigger></Tabs.List>
    <Tabs.Content value="signals">
      <section class="position-control">
        <label>Readout turn <select aria-label="Readout turn" value={turn?.request??''} onchange={ev=>{const t=pi.turns.find(t=>t.request===ev.currentTarget.value);const r=t?.records.at(-1);if(t&&r)live.select?.({request:t.request,pos:r.pos});}}>{#each pi.turns as t}<option value={t.request}>Turn {t.turn} · {t.status}</option>{/each}</select></label>
        <div class="position-summary"><span>{live.selection?'Frozen position':'Following latest'}</span><button onclick={()=>live.select?.()} disabled={!live.selection}>Follow</button></div>
        <input type="range" aria-label="Live consumed position" min="0" max={Math.max(0,rows.length-1)} value={Math.max(0,index)} disabled={!rows.length} oninput={ev=>rowAt(Number(ev.currentTarget.value))}/>
        <p>{chosen?`Position ${chosen.record.pos} · ${chosen.record.source} · ${chosen.record.channel??'unknown channel'}`:'No measured position received.'}</p>
        {#if chosen}<code class="token-label">{live.labels?.[chosen.record.token]??`#${chosen.record.token} (label unavailable)`}</code>
          {#each live.alerts?.events.filter(a=>a.request===chosen.turn.request&&a.pos===chosen.record.pos)??[] as alert}<p class="signal-badge" title={alert.detail}>{alert.match?`${alert.match.label.trim()} entered · `:''}{alert.rule}</p>{/each}
        {/if}
      </section>
      <section><div class="title"><h3>Global workspace</h3><span>TOP {w?.k??'—'}</span></div>
        <p>Interpreted J-lens candidates, not a thought transcript.</p>
        <label>Workspace layer <select aria-label="Workspace display layer" value={wLayer??''} onchange={ev=>workspaceLayer=Number(ev.currentTarget.value)} disabled={!w}>{#each w?.layers??[] as l}<option value={l}>L{l}</option>{/each}</select></label>
        {#if top}<ol class="workspace-tokens">{#each top as [id,score],rank}<li><span class="rank">{rank+1}</span><code title={`Token ID ${id}`}>{live.labels?.[id]??`#${id}`}</code><span class="score">{score.toFixed(3)}</span></li>{/each}</ol>
        {:else}<div class="unavailable">{w?'Workspace unavailable at this position.':'Workspace not enabled for this turn.'} Missing is not zero.</div>{/if}
        <p>Ranks are within the supplied top-k only. Scores are lens logits, not probabilities. Isolated token labels may contain partial characters.</p>
      </section>
      <section><div class="title"><h3>Emotion directions</h3><span>L{eLayer??'—'}</span></div><p>Raw signed projections. Layers and names come from this turn’s header.</p>
        {#if e&&chosen&&eLayer!==undefined}<div class="projections">{#each e.names as name}{@const value=projection(chosen.turn,chosen.record,eLayer,name)}<div><span>{name}</span><strong>{value===null?'unavailable':value.toFixed(3)}</strong></div>{/each}</div>{:else}<div class="unavailable">No emotion measurements at this position.</div>{/if}
      </section>
    </Tabs.Content>
    <Tabs.Content value="control">
      <section><div class="title"><h3>Next request</h3><span>v{pi.settings?.version??'—'}</span></div>
        <p>Apply saves a versioned Pi setting. It does not alter a request already running. Inspect the turn header for what actually ran.</p>
        <p data-testid="readout-setting">{!pi.settings?'Loading saved settings…':!pi.settings.readout.enabled?'Saved: readouts off.':`Saved: emotion L${pi.settings.readout.emotion?.layers.join(', L')}; workspace ${pi.settings.readout.workspace?`L${pi.settings.readout.workspace.layers.join(', L')}, top-${pi.settings.readout.workspace.k}`:'off'}.`} The form edits one capture layer at a time.</p>
        {#if !live.assets?.supported}<p class="unavailable">{live.assets?.error??'Asset capabilities unavailable.'}</p>{/if}
        <fieldset disabled={!d||!live.assets?.supported||pending||!pi.connected}><legend>Readout capture</legend>
          <label>Emotion capture layer <select aria-label="Emotion capture layer" bind:value={emotionLayer}>{#each d?.layers??[] as l}<option value={l}>L{l}</option>{/each}</select></label>
          <label class="check"><input type="checkbox" bind:checked={workspace} disabled={!lens}/>Workspace top-16</label>
          <label>Workspace capture layer <select aria-label="Workspace capture layer" bind:value={workspaceLayer} disabled={!lens||!workspace}>{#each lens?.layers??[] as l}<option value={l}>L{l}</option>{/each}</select></label>
          <button onclick={readouts}>Apply readout settings</button>
        </fieldset>
        <button disabled={pending||!pi.connected} onclick={()=>configure('readout',{enabled:false,emotion:null,workspace:null})}>Disable readouts</button>
      </section>
      <section><fieldset disabled={!d||!live.assets?.supported||pending||!pi.connected}><legend>Signed steering</legend>
        <label>Direction <select aria-label="Steering direction" bind:value={direction}>{#each d?.names??[] as name}<option value={name}>{name}</option>{/each}</select></label>
        <label>Intervention layer <select aria-label="Steering layer" bind:value={steerLayer}>{#each d?.layers??[] as l}<option value={l}>L{l}</option>{/each}</select></label>
        <label>Strength <input aria-label="Signed steering strength" type="number" min="-2" max="2" step="0.1" bind:value={strength}/></label>
        <p>Negative steers away. Strength is a signed multiplier of the asset’s layer size. Range −2 to +2.</p><button onclick={steer}>Apply steering</button>
      </fieldset><button disabled={pending||!pi.connected} onclick={()=>configure('steer',null)}>Clear steering</button>
      <p class="applied" data-testid="steer-setting">Saved v{pi.settings?.version??'—'}: {pi.settings?.steer?`${pi.settings.steer.direction} ${pi.settings.steer.strength>0?'+':''}${pi.settings.steer.strength} at L${pi.settings.steer.layer}`:'steering off'}</p>
      </section>
      {#if error}<p role="alert">{error}</p>{/if}
    </Tabs.Content>
    <Tabs.Content value="alerts"><AlertControls {live}/></Tabs.Content>
    <Tabs.Content value="profile"><AgentProfile {live}/></Tabs.Content>
    <Tabs.Content value="status"><section><h3>Measured, not illustrative</h3><p>Bridge {pi.connected?'connected':'disconnected'} · session {live.conversation??'—'}. Text and consumed-position readouts arrive on separate clocks; SSE chunks are not tokens.</p>
      {#each pi.turns as t}<details class="turn-status"><summary>Turn {t.turn} · {t.status} · {t.records.length} rows</summary><p>{t.error??'No reported readout error.'}</p><pre>{JSON.stringify({request:t.request,header:t.header,summary:t.summary},null,2)}</pre></details>{/each}
      {#if chosen?.record.missing.length}<p>Position missing: {chosen.record.missing.join(', ')}</p>{/if}
      {#each pi.errors as message}<p role="alert">{message}</p>{/each}
      <p>Unknown stays unknown. A completed text reply does not imply a complete readout. Local token labels use the operator-pinned tokenizer, not an attestation of GPU-resident bytes.</p>
    </section></Tabs.Content>
  </Tabs.Root>
  <footer>Interpretability signals are not a safety guarantee or a lie detector.</footer>
</div>
<style>
  .live-inspector{overflow:auto;min-height:0;flex:1;font-size:12px;color:var(--color-ink-2)}.live-inspector section{padding:18px;border-bottom:1px solid var(--color-rule)}.live-inspector p{font-size:11px;line-height:1.7;color:var(--color-muted);margin:8px 0}.title{display:flex;align-items:center;justify-content:space-between;gap:8px}.title h3,.live-inspector h3{font-size:13px;font-weight:500}.title>span{font:10px var(--font-mono);color:var(--color-accent)}.live-inspector label{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:10px 0}.live-inspector select,.live-inspector input[type=number]{min-width:68px;max-width:150px;background:var(--color-paper-2);color:var(--color-ink);border:1px solid var(--color-rule);border-radius:5px;padding:7px}.live-inspector input[type=range]{width:100%;accent-color:var(--color-accent)}.live-inspector button{padding:7px 10px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink);margin:4px 4px 0 0;font-size:11px}.live-inspector button:disabled{opacity:.5}.live-inspector fieldset{border:0;padding:0;margin:8px 0}.live-inspector legend{font-weight:500;margin-bottom:4px}.live-inspector .check{justify-content:flex-start}.position-summary{display:flex;align-items:center;justify-content:space-between;font-size:10px;color:var(--color-muted)}.token-label{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--color-accent)}.workspace-tokens{list-style:none;padding:0;margin:14px 0}.workspace-tokens li{display:flex;gap:9px;align-items:center;border-bottom:1px solid var(--color-rule);padding:6px 0}.workspace-tokens .rank{color:var(--color-muted);width:18px;font-size:10px}.workspace-tokens code{white-space:pre-wrap;overflow-wrap:anywhere;flex:1;color:var(--color-ink)}.score{font:10px var(--font-mono);color:var(--color-muted)}.unavailable{border:1px dashed var(--color-rule);padding:15px;margin:12px 0;line-height:1.7;color:var(--color-muted)}.projections{display:grid;gap:6px;max-height:300px;overflow:auto;margin-top:12px}.projections>div{display:flex;justify-content:space-between;gap:8px}.projections strong{font:11px var(--font-mono);font-weight:400}.applied{color:var(--color-accent)!important}.turn-status{margin:14px 0}.turn-status summary{cursor:pointer}.turn-status pre{font-size:10px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:350px;overflow:auto}.live-inspector footer{padding:18px;color:var(--color-muted);font-size:10px;line-height:1.7}
</style>
