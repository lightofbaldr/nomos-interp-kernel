<script lang="ts">
  import {onMount} from 'svelte';
  import type {Snippet} from 'svelte';
  let {children}:{children:Snippet}=$props();
  let effect=$state<typeof import('@nomos/svelte-magic/blur-fade').BlurFade|null>(null),reduced=$state(true);
  onMount(()=>{let disposed=false;const query=matchMedia('(prefers-reduced-motion: reduce)');const change=()=>reduced=query.matches;change();query.addEventListener('change',change);import('@nomos/svelte-magic/blur-fade').then(m=>{if(!disposed)effect=m.BlurFade;});return()=>{disposed=true;query.removeEventListener('change',change);};});
</script>
{#if effect&&!reduced}{@const Effect=effect}<Effect class="welcome-reveal" blur="0px" offset={8} duration={0.3} triggerOnView={false}>{@render children()}</Effect>{:else}{@render children()}{/if}
