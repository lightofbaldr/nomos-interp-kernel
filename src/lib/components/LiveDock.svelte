<script lang="ts">
  import type {LiveState} from '$lib/live';
  import {emptyPi,selectedRow,projection} from '$lib/pi/model';
  let {live}:{live:LiveState}=$props();
  const chosen=$derived(selectedRow(live.pi??emptyPi(),live.selection));
  const setting=$derived(chosen?.turn.header?.settings?.emotion);
  const layer=$derived(setting?.layers.includes(live.emotionLayer??29)?live.emotionLayer??29:setting?.layers.at(-1));
  const names=['joyful','calm','sad','afraid','angry','desperate','compassionate'];
</script>
<section class="live-dock" aria-label="Live emotion readouts">
  <div class="dock-heading"><h2>Emotion directions</h2><span>LIVE · PROJECTION</span>
    <label>Layer <select aria-label="Emotion display layer" value={layer??''} onchange={e=>live.selectLayer?.(Number(e.currentTarget.value))} disabled={!setting}>{#each setting?.layers??[] as id}<option value={id}>L{id}</option>{/each}</select></label>
  </div>
  <div class="dock-values">{#each names as name}{@const value=chosen&&layer!==undefined?projection(chosen.turn,chosen.record,layer,name):null}<div><span>{name}</span><strong class:negative={value!==null&&value<0}>{value===null?'—':value.toFixed(2)}</strong></div>{/each}</div>
  <p>{chosen?`Turn ${chosen.turn.turn} · consumed position ${chosen.record.pos} · ${chosen.record.channel??'channel unknown'}`:'Waiting for measured positions.'} Signed alignment, not an emotion probability. — means unavailable.</p>
</section>
<style>
  .live-dock{border-top:1px solid var(--color-rule);padding:8px 0;color:var(--color-ink-2)}.dock-heading{display:flex;gap:10px;align-items:center}.dock-heading h2{font-size:12px;font-weight:500}.dock-heading>span{font:9px var(--font-mono);color:var(--color-muted)}.dock-heading label{margin-left:auto;font-size:11px}.dock-heading select{background:var(--color-paper-2);color:var(--color-ink);border:1px solid var(--color-rule);padding:4px;border-radius:4px}.dock-values{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));margin:10px 0;gap:8px}.dock-values>div{display:grid;gap:5px;text-align:center;border-left:1px solid var(--color-rule)}.dock-values>div:first-child{border:0}.dock-values span{font-size:10px;overflow:hidden;text-overflow:ellipsis}.dock-values strong{font:16px var(--font-mono);color:var(--color-accent)}.dock-values strong.negative{color:var(--color-ink)}.live-dock p{font-size:10px;line-height:1.6;color:var(--color-muted)}
  @media(max-width:36rem){.dock-values{grid-template-columns:repeat(4,minmax(0,1fr));row-gap:12px}.dock-heading>span{display:none}}
</style>
