<script lang="ts">
  import type {LiveState} from '$lib/live';
  let {live}:{live:LiveState}=$props();
</script>
{#if live.alerts?.total}
  <section class="signal-trail" aria-label="Signal alert badges">
    <h3>Signal alerts <span>{live.alerts.total}</span></h3>
    <p>Internal-signal rules, not a lie detector. Badges link to consumed positions, not text characters.</p>
    {#each live.pi?.turns??[] as t}{#if live.alerts.counts[t.request]}
      <details><summary>Turn {t.turn} <span class="alert-count">{live.alerts.counts[t.request]} signal alerts</span> · {t.status}</summary>
        <div class="badges">{#each live.alerts.events.filter(e=>e.request===t.request) as alert (alert.id)}
          <button title={`${alert.rule}. ${alert.detail}`} onclick={()=>live.select?.({request:alert.request,pos:alert.pos})}>
            <span>{alert.kind==='emotion'?'Projection crossing':'Watch-list entry'}</span><code>pos {alert.pos} · {live.labels?.[alert.token]??`#${alert.token}`}</code>
          </button>
        {/each}</div>
      </details>
    {/if}{/each}
    <p>Rules apply to this loaded conversation, including earlier records. Showing the latest {live.alerts.events.length} badges (maximum 200); totals include earlier matches. Incomplete turns remain provisional.</p>
  </section>
{/if}
<style>
  .signal-trail{margin:20px 0;border-top:1px solid var(--color-rule);padding-top:12px;font-size:11px;color:var(--color-ink-2)}.signal-trail h3{font-size:12px;font-weight:500;display:flex;gap:8px;align-items:center}.signal-trail h3>span,.alert-count{border:1px solid var(--color-rule);padding:2px 6px;border-radius:8px;font:10px var(--font-mono);color:var(--color-accent)}.signal-trail p{font-size:10px;line-height:1.7;color:var(--color-muted);margin:8px 0}.signal-trail details{margin:12px 0}.signal-trail summary{cursor:pointer;line-height:2}.badges{display:flex;flex-wrap:wrap;gap:6px;padding-top:8px}.badges button{display:grid;gap:4px;max-width:100%;border:1px solid var(--color-rule);border-radius:6px;background:var(--color-paper-2);color:var(--color-ink);padding:8px;text-align:left;font-size:10px}.badges code{font-size:10px;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--color-accent)}
</style>
