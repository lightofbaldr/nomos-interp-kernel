<script lang="ts">
  import type {LiveState} from '$lib/live';
  let {live}:{live:LiveState}=$props();
  let fullTrail=$state(false);
  const shown=$derived(fullTrail?live.alerts?.events.map(first=>({first,entries:1,lastPos:first.pos}))??[]:live.alerts?.groups??[]);
</script>
{#if live.alerts?.total}
  <section class="signal-trail" aria-label="Signal alert badges">
    <h3>Signal alerts <span>{live.alerts.groupTotal} grouped · {live.alerts.total} entries</span></h3>
    <p>Internal-signal rules, not a lie detector. Badges link to consumed positions, not text characters.</p>
    <label class="trail-toggle"><input type="checkbox" bind:checked={fullTrail}/>Show detailed entry trail</label>
    {#each live.pi?.turns??[] as t}{#if live.alerts.counts[t.request]}
      <details><summary>Turn {t.turn} <span class="alert-count">{live.alerts.groupCounts[t.request]} grouped · {live.alerts.counts[t.request]} entries</span> · {t.status}</summary>
        <div class="badges">{#each shown.filter(g=>g.first.request===t.request) as group (group.first.id)}
          {@const alert=group.first}
          {@const reentryText=`${group.entries-1} re-${group.entries===2?'entry':'entries'}`}
          <button aria-label={`${alert.match?`Watch-list entry: ${alert.match.label.trim()}`:'Projection crossing'}, position ${alert.pos}${group.entries>1?`, ${reentryText}`:''}`} title={`${alert.rule}. ${alert.detail}${group.entries>1?` First entry here; ${reentryText}, last at position ${group.lastPos}.`:''}`} onclick={()=>live.select?.({request:alert.request,pos:alert.pos})}>
            <span>{alert.match?`${alert.match.label.trim()} entered`:'Projection crossing'}</span><code>pos {alert.pos}{#if !alert.match} · {live.labels?.[alert.token]??`#${alert.token}`}{/if}</code>
            {#if group.entries>1}<span class="reentries">+{group.entries-1} re-{group.entries===2?'entry':'entries'}</span>{/if}
          </button>
        {/each}</div>
      </details>
    {/if}{/each}
    <p>{fullTrail?'Each observed entry is shown, including re-entries.':'First entry per watched prefix and turn, with re-entry counts. The label is the first matched workspace token, not the consumed token.'} Showing the latest {shown.length} {fullTrail?'entries':'groups'} (maximum 200); totals include earlier matches. Incomplete turns remain provisional.</p>
  </section>
{/if}
<style>
  .signal-trail .trail-toggle{display:flex;align-items:center;gap:7px;margin:12px 0;font-size:11px}.signal-trail .trail-toggle input{accent-color:var(--color-accent)}.signal-trail .reentries{color:var(--color-muted)}
  .signal-trail{margin:20px 0;border-top:1px solid var(--color-rule);padding-top:12px;font-size:11px;color:var(--color-ink-2)}.signal-trail h3{font-size:12px;font-weight:500;display:flex;gap:8px;align-items:center}.signal-trail h3>span,.alert-count{border:1px solid var(--color-rule);padding:2px 6px;border-radius:8px;font:10px var(--font-mono);color:var(--color-accent)}.signal-trail p{font-size:10px;line-height:1.7;color:var(--color-muted);margin:8px 0}.signal-trail details{margin:12px 0}.signal-trail summary{cursor:pointer;line-height:2}.badges{display:flex;flex-wrap:wrap;gap:6px;padding-top:8px}.badges button{display:grid;gap:4px;max-width:100%;border:1px solid var(--color-rule);border-radius:6px;background:var(--color-paper-2);color:var(--color-ink);padding:8px;text-align:left;font-size:10px}.badges code{font-size:10px;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--color-accent)}
</style>
