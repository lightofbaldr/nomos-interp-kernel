<script lang="ts">
  import {onMount} from 'svelte';
  let {label}:{label:string}=$props();
  let effect=$state<typeof import('@nomos/svelte-fancy/letter-3d-swap').Letter3DSwap|null>(null);
  let reduced=$state(true);
  onMount(()=>{let disposed=false;const query=matchMedia('(prefers-reduced-motion: reduce)');const change=()=>reduced=query.matches;change();query.addEventListener('change',change);import('@nomos/svelte-fancy/letter-3d-swap').then(m=>{if(!disposed)effect=m.Letter3DSwap;});return()=>{disposed=true;query.removeEventListener('change',change);};});
</script>
<span class="motion-label">{#if effect&&!reduced}{@const Effect=effect}<span aria-hidden="true"><Effect as="span" staggerDuration={0.018} transition={{duration:0.22,type:'tween'}}>{label}</Effect></span><span class="sr-only">{label}</span>{:else}{label}{/if}</span>
