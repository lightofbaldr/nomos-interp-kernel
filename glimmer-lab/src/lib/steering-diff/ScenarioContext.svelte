<script lang="ts">
  import { Accordion } from 'bits-ui';
  import { CaretDown } from 'phosphor-svelte';
  import type { Mode, Scenario } from './model';
  let { scenario, related, onopen }: { scenario: Scenario; related: Scenario[]; onopen:(key:string,mode:Mode)=>void } = $props();
</script>
{#if scenario.source}
  <section class="scenario-context" aria-label="Recorded episode provenance">
    <div class="scenario-summary"><span class="eyebrow">RECORDED SOURCE EPISODE</span><span>Historical label <strong>{scenario.source.label ?? 'Not supplied'}</strong></span><span>Classifier <strong>{scenario.source.classifier ?? 'Not supplied'}</strong></span></div>
    <p class="scenario-caveat">These labels belong to the recorded source, not to the newly generated arms. This scenario supplies {scenario.modes.map(m=>m==='matched'?'matched replay':'free generation').join(' and ')} only.</p>
    {#if related.length}<div class="paired-scenarios"><span>Same source episode, different task:</span>{#each related as s}{#each s.modes as m}<button onclick={()=>onopen(s.key,m)}>Open {s.key} · {m==='matched'?'matched replay':'free answer'} <span aria-hidden="true">↗</span></button>{/each}{/each}</div><p class="scenario-caveat">Replay and answer scenarios have different prefixes; their positions are not aligned to each other.</p>{/if}
    <Accordion.Root type="single"><Accordion.Item value="source"><Accordion.Header><Accordion.Trigger class="flex w-full items-center justify-between gap-3 pt-3 text-left text-xs text-[#aab2bf]">Source identity & recorded boundaries<CaretDown size={14}/></Accordion.Trigger></Accordion.Header><Accordion.Content><dl class="scenario-boundaries"><dt>Original prompt length</dt><dd>{scenario.source.prompt_len}</dd><dt>Recorded answer start</dt><dd>{scenario.source.answer_start??'Not supplied'}</dd><dt>Recorded thinking-tail setting</dt><dd>{scenario.source.thinking_tail??'Not supplied'}</dd><dt>This scenario's prefill length</dt><dd>{scenario.P}</dd></dl><pre class="identity-json">{JSON.stringify(scenario,null,2)}</pre></Accordion.Content></Accordion.Item></Accordion.Root>
  </section>
{/if}
