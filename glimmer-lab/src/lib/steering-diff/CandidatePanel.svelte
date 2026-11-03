<script lang="ts">
  import { ArrowRight, ArrowUpRight, ArrowDownRight } from 'phosphor-svelte';
  import { topCandidates, differenceGroups, scoreText, deltaText, deltaClass, tokenText } from './model';
  import type { Candidate } from './model';
  let { side, candidates, vocab, displayK, transmittedK, selected, onselect }: {
    side: 'baseline' | 'steered' | 'difference'; candidates: Candidate[]; vocab: Record<string,string>;
    displayK: number; transmittedK: number; selected: number | null; onselect: (id:number)=>void;
  } = $props();
  const rows = $derived(side === 'difference' ? [] : topCandidates(candidates, side, displayK));
  const groups = $derived(differenceGroups(candidates));
</script>

<section class="candidate-panel {side}" aria-label={`${side} candidates`}>
  <header>
    <div class="panel-title"><span class="arm-dot" aria-hidden="true"></span><h3>{side === 'difference' ? 'Difference' : side === 'baseline' ? 'Baseline' : 'Steered'}</h3></div>
    <span class="mono panel-note">{side === 'difference' ? 'STEERED − BASELINE' : `DISPLAYED TOP ${displayK}`}</span>
  </header>
  {#if side !== 'difference'}
    <div class="table-labels"><span>Rank / token</span><span>J-lens score</span></div>
    <div class="candidate-list">
      {#each rows as c (c.id)}
        <button class="candidate-row" class:selected={selected === c.id} onclick={() => onselect(c.id)} aria-pressed={selected === c.id} title={`Token ${c.id}: ${JSON.stringify(vocab[c.id] ?? null)}. Rank within transmitted top ${transmittedK}.`}>
          <span class="rank mono">{c[`${side}_rank`]}</span>
          <span class="token-info"><code>{tokenText(vocab,c.id)}</code><small class="mono">#{c.id}</small></span>
          <span class="score mono">{scoreText(c,side)}</span>
        </button>
      {/each}
    </div>
    <footer>Rank within the transmitted top {transmittedK}.<br />Not a full-vocabulary rank or probability.</footer>
  {:else}
    <div class="table-labels"><span>Token / top-{displayK} membership</span><span>Signed Δ</span></div>
    <div class="difference-list">
      {#each groups.exact as c (c.id)}
        {@render differenceRow(c)}
      {/each}
      {#if groups.bounded.length}
        <div class="bound-label">Bounds · not ordered against exact deltas</div>
        {#each groups.bounded as c (c.id)}{@render differenceRow(c)}{/each}
      {/if}
    </div>
    <footer>Exact deltas sorted high → low. Bounds kept separate.<br />Unknown scores are never treated as zero.</footer>
  {/if}
</section>

{#snippet differenceRow(c: Candidate)}
  <button class="difference-row {deltaClass(c)}" class:selected={selected === c.id} onclick={() => onselect(c.id)} aria-pressed={selected === c.id} title={`Token ${c.id}. Baseline ${scoreText(c,'baseline')}; steered ${scoreText(c,'steered')}.`}>
    <span class="change-icon" aria-hidden="true">{#if deltaClass(c) === 'increase'}<ArrowUpRight size={15}/>{:else if deltaClass(c) === 'decrease'}<ArrowDownRight size={15}/>{:else}<ArrowRight size={15}/>{/if}</span>
    <span class="token-info"><code>{tokenText(vocab,c.id)}</code><small>{c.status === 'both' ? `In both displayed top ${displayK}` : `${c.status === 'entered' ? 'Entered' : 'Left'} displayed top ${displayK}`} <span class="mono">· #{c.id}</span></small></span>
    <span class="score mono">{deltaText(c)}</span>
  </button>
{/snippet}
