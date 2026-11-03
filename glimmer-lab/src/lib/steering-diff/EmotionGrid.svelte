<script lang="ts">
  import { numberText } from './model';
  import type { Position, Settings } from './model';
  let { position, settings, intervention }: { position: Position; settings: Settings; intervention: number } = $props();
  let showAll = $state(false);
  const featured = ['joyful','hopeful','compassionate','calm','desperate','afraid','sad','anxious'];
  const names = $derived(showAll ? settings.emotion.names : featured.filter(n => settings.emotion.names.includes(n)));
  function value(name:string, li:number) { return position.emotion_delta?.[li]?.[settings.emotion.names.indexOf(name)] ?? null; }
</script>
<section class="emotion-section" aria-label="Direction alignment differences">
  <div class="section-heading"><div><span class="eyebrow">DIRECTION READOUT</span><h2>What changes across depth</h2></div><button class="text-button" onclick={() => showAll=!showAll}>{showAll ? 'Show focus directions' : `Show all ${settings.emotion.names.length} directions`}</button></div>
  <p class="section-description">Recorded alignment differences at this consumed position. These are projections, not emotional states.</p>
  <div class="heatmap-scroll">
    <table class="heatmap"><caption class="sr-only">Steered minus baseline direction alignments, by layer</caption><thead><tr><th scope="col">Direction</th>{#each settings.emotion.layers as layer}<th scope="col" class:intervention={layer===intervention}>L{layer}{#if layer===intervention}<span>injection</span>{/if}</th>{/each}</tr></thead><tbody>
      {#each names as name}<tr><th scope="row">{name}</th>{#each settings.emotion.layers as layer, li}{@const v=value(name,li)}<td class:intervention={layer===intervention} class:increase={v!==null && v>0} class:decrease={v!==null && v<0} style:--strength={v===null?0:Math.min(.23,Math.abs(v)/180)}><span class="mono">{numberText(v,true)}</span></td>{/each}</tr>{/each}
    </tbody></table>
  </div>
  <div class="method-note"><span class="injection-marker" aria-hidden="true"></span><p><strong>L{intervention} is the intervention layer.</strong> Its change includes the injected vector itself. It is not independent evidence of a downstream response. Later-layer values are not a connectivity or topology measurement.</p></div>
</section>
