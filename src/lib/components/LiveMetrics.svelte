<script lang="ts">
  import type {LiveState} from '$lib/live';
  let {state}:{state:LiveState}=$props();
  const integer=new Intl.NumberFormat('en-US');
  function bytes(n:number){if(n<1024)return `${n} B`;const unit=n>=1073741824?1073741824:1048576;return `${(n/unit).toFixed(2)} ${unit===1073741824?'GiB':'MiB'}`;}
</script>
<section class="live-kernel-metrics" aria-label="Live kernel metrics">
  <div class="readout-title"><h3>Kernel connection</h3><span class="readout-kind">SERVER-REPORTED</span></div>
  <dl class="live-metric-list"><dt>Model</dt><dd>{state.health?.model??'Not available'}</dd><dt>Engine</dt><dd>{state.health?.engine??'Not connected'}</dd><dt>DFlash</dt><dd>{state.health?(state.health.spec?'Loaded':'Not loaded'):'Unknown'}</dd><dt>KV store</dt><dd>{state.health?.store??'Unknown'}</dd><dt>Last reply</dt><dd>{state.busy?'Generating':state.finish??'No request yet'}</dd></dl>
  {#if state.firstTextMs!==null}<p class="readout-subtitle live-timing">First streamed text {integer.format(state.firstTextMs)} ms{state.elapsedMs!==null?` · request ${(state.elapsedMs/1000).toFixed(1)} s`:''}. Browser-observed; not kernel token throughput.</p>{/if}
</section>
<section class="cache-section" aria-label="KV session store counters">
  <div class="readout-title"><h3>KV session store</h3><span class="readout-kind">{state.busy?'SNAPSHOT':'CUMULATIVE'}</span></div>
  {#if state.stats&&state.health?.store==='open'}
    <dl class="live-metric-list kv-counters"><dt>RAM resident</dt><dd>{bytes(state.stats.ram_bytes)}</dd><dt>NVMe resident</dt><dd>{bytes(state.stats.nvme_bytes)}</dd><dt>Prompt tokens</dt><dd>{integer.format(state.stats.prompt_tokens)}</dd><dt>Tokens reused</dt><dd>{integer.format(state.stats.reused_tokens)}</dd><dt>RAM restores</dt><dd>{integer.format(state.stats.restores_ram)}</dd><dt>NVMe restores</dt><dd>{integer.format(state.stats.restores_nvme)}</dd><dt>Forks</dt><dd>{integer.format(state.stats.forks)}</dd><dt>RAM evictions</dt><dd>{integer.format(state.stats.ram_evictions)}</dd><dt>NVMe evictions</dt><dd>{integer.format(state.stats.nvme_evictions)}</dd><dt>Requests prepared</dt><dd>{integer.format(state.stats.prepares)}</dd></dl>
    <p class="interpretation-note">Bytes are current occupancy. Other counters are totals since store startup, shared across clients—not attributed to this conversation. {state.busy?'Snapshot held during generation.':'Refreshed only between turns.'}</p>
    {#if state.statsAt}<p class="readout-subtitle">Last fetched {new Date(state.statsAt).toLocaleTimeString()}</p>{/if}
  {:else}<div class="emotion-placeholder"><span>{state.statsError||'KV counters unavailable'}</span><span class="mono">Unknown</span></div>{/if}
</section>
