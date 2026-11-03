<script lang="ts">
  import {onMount} from 'svelte';
  import {labContext} from '$lib/workspace/context';
  import {bytes,fraction,type SystemSnapshot} from '$lib/inspection/system';
  import '$lib/inspection/pages.css';
  const context=labContext();
  let data=$state<SystemSnapshot|null>(null),loading=$state(false),error=$state(''),alive=true;
  const cache=$derived(data?.cache.stats),host=$derived(data?.host),health=$derived(data?.health);
  async function refresh(withCache=false){
    if(loading)return;loading=true;error='';
    try{const r=await fetch(`/api/system${withCache?'?cache=1':''}`,{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('System snapshot unavailable.');const value=await r.json();if(alive)data=value;}
    catch{if(alive)error='System snapshot unavailable. The last reading, if present, is kept.';}finally{if(alive)loading=false;}
  }
  onMount(()=>{alive=true;void refresh();return()=>{alive=false;};});
  const num=(v:number|null|undefined)=>v===null||v===undefined?'Unavailable':v.toLocaleString('en-US');
  const when=(s:string|null|undefined)=>s?new Date(s).toLocaleTimeString(): 'Not sampled';
</script>
<svelte:head><title>System · Glimmer Lab</title><meta name="description" content="Read-only kernel health, explicit cache snapshots and local UI-host resource readings."/></svelte:head>
<div class="inspection-page system-page">
  <header class="page-head"><div><span class="eyebrow">RUNTIME &amp; RESOURCES</span><h1>System</h1><p class="lede">Kernel health and cache activity, with the UI host’s resources shown separately.</p></div><button class="action" onclick={()=>refresh()} disabled={loading}>{loading?'Reading…':'Refresh health & host'}</button></header>
  <div class="inspection-source"><span>{data?.endpoint??'No kernel endpoint'}</span><span class="source-status">{data?`Health / host sampled ${when(data.at)}`:'Reading system snapshot…'}</span></div>
  {#if error}<p class="notice" role="alert">{error}</p>{/if}
  {#if data?.healthError}<p class="notice" role="status">{data.healthError}</p>{/if}
  <div class="metric-row">
    <div class="metric"><span>Kernel</span><strong>{!data?'…':health?health.ready?'Ready':'Not ready':'Unknown'}</strong><small>{health?.model??'No verified health response'}</small></div>
    <div class="metric"><span>Live readouts</span><strong>{health?.readout===true?'Supported':health?.readout===false?'Off':'Unknown'}</strong><small>{health?.assets?`${health.assets.length} registered assets`:'No verified asset inventory'}</small></div>
    <div class="metric"><span>Prefix store</span><strong>{health?.store??'Unknown'}</strong><small>Kernel-reported state</small></div>
    <div class="metric"><span>Speculative decode</span><strong>{health?.spec===true?'On':health?.spec===false?'Off':'Unknown'}</strong><small>Not an acceptance-rate measurement</small></div>
  </div>
  <div class="two-columns">
    <section class="panel"><header><h2>Kernel cache</h2><button class="action" onclick={()=>refresh(true)} disabled={loading||context.live.busy}>Read cache counters</button></header>
      <p class="muted">Manual snapshots only. This request can wait for the engine; local Pi runs defer it. Other clients may still be using the kernel.</p>
      <p class="notice" role="status">{context.live.busy?'This conversation is generating; cache refresh is paused.':data?.cache.message??'Cache counters have not been requested.'}</p>
      <dl><div><dt>Reused / prepared prompt tokens</dt><dd class="number">{cache?fraction(cache.reused_tokens,cache.prompt_tokens):'Not sampled'}</dd></div><div><dt>Reused tokens</dt><dd class="number">{num(cache?.reused_tokens)}</dd></div><div><dt>Prompt tokens</dt><dd class="number">{num(cache?.prompt_tokens)}</dd></div><div><dt>Prepares / forks</dt><dd class="number">{cache?`${num(cache.prepares)} / ${num(cache.forks)}`:'Not sampled'}</dd></div><div><dt>RAM / NVMe restores</dt><dd class="number">{cache?`${num(cache.restores_ram)} / ${num(cache.restores_nvme)}`:'Not sampled'}</dd></div><div><dt>RAM / NVMe cache</dt><dd class="number">{cache?`${bytes(cache.ram_bytes)} / ${bytes(cache.nvme_bytes)}`:'Not sampled'}</dd></div><div><dt>RAM / NVMe evictions</dt><dd class="number">{cache?`${num(cache.ram_evictions)} / ${num(cache.nvme_evictions)}`:'Not sampled'}</dd></div></dl>
      <p class="page-note">{cache?`Last successful sample: ${when(data?.cache.at)}. `:''}These are kernel-wide counters, not this conversation’s cache hit rate. Refresh is limited to once per 65 seconds; there is no background polling.</p>
    </section>
    <section class="panel" aria-label="UI host resources"><header><h2>UI host</h2><span>{host?.hostname??'UNAVAILABLE'}</span></header><p class="muted">This is the machine hosting Glimmer Lab—not necessarily the machine running Glimmer. A localhost tunnel does not make their hardware the same.</p>
      <dl><div><dt>RAM available / total</dt><dd class="number">{host?.memory?`${bytes(host.memory.available)} / ${bytes(host.memory.total)}`:'Unavailable'}</dd></div><div><dt>Swap used / total</dt><dd class="number">{host?.memory?`${bytes(host.memory.swapTotal-host.memory.swapFree)} / ${bytes(host.memory.swapTotal)}`:'Unavailable'}</dd></div><div><dt>Memory pressure · full avg10</dt><dd class="number">{host?.pressure?.full!==null&&host?.pressure?.full!==undefined?`${host.pressure.full.toFixed(2)}%`:'Unavailable'}</dd></div><div><dt>Session volume · available / total</dt><dd class="number">{host?.disk?`${bytes(host.disk.available)} / ${bytes(host.disk.total)}`:'Unavailable'}</dd></div></dl>
      <div class="local-gpus"><h3>Local GPUs · not backend telemetry</h3>{#if host?.gpus}{#each host.gpus as gpu}<p>{gpu.name}</p><dl><div><dt>VRAM used / total</dt><dd class="number">{num(gpu.usedMiB)} / {num(gpu.totalMiB)} MiB</dd></div><div><dt>Utilization / temperature</dt><dd class="number">{gpu.utilization===null?'Unknown':`${gpu.utilization}%`} / {gpu.temperature===null?'Unknown':`${gpu.temperature} °C`}</dd></div></dl>{/each}{:else}<p class="muted">Local GPU readings unavailable.</p>{/if}</div>
      <p class="page-note">{host?.platform??'Local platform unavailable.'} Pressure is a sampled stall percentage, not a safety guarantee. Remote GPU memory, power and temperature are not supplied by this kernel endpoint.</p>
    </section>
  </div>
  <section class="panel"><header><h2>Readout asset identities</h2><span>REPORTED BY THE KERNEL</span></header>{#if health?.assets?.length}<ul class="asset-list">{#each health.assets as id}<li><code>{id}</code></li>{/each}</ul>{:else}<p class="muted">No verified asset identities available.</p>{/if}</section>
</div>
<style>.local-gpus{margin-top:22px}.local-gpus>p{margin-top:12px}.asset-list{list-style:none;padding:0;font:11px/2 var(--font-mono);color:var(--color-muted);overflow-wrap:anywhere}.system-page .two-columns{grid-template-columns:1fr 1fr}@media(max-width:950px){.system-page .two-columns{grid-template-columns:1fr}}</style>
