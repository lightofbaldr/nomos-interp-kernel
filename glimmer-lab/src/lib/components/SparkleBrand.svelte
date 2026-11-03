<script lang="ts">
  import {onMount} from 'svelte';
  import {SparklesText} from '@nomos/svelte-magic/sparkles-text';
  import {Pause,Play} from 'phosphor-svelte';

  let {paused=$bindable(false)}:{paused?:boolean}=$props();
  let root:HTMLDivElement;
  // SSR and the first hydration frame are ordinary, readable text.
  let reduced=$state(true),visible=$state(false),inView=$state(false);
  const colors={first:'var(--color-accent)',second:'var(--color-focus)'};

  onMount(()=>{
    const query=matchMedia('(prefers-reduced-motion: reduce)');
    const updateMotion=()=>reduced=query.matches;
    const updateVisibility=()=>visible=document.visibilityState==='visible';
    const observer=new IntersectionObserver(([entry])=>inView=entry.isIntersecting);
    updateMotion();updateVisibility();observer.observe(root);
    query.addEventListener('change',updateMotion);
    document.addEventListener('visibilitychange',updateVisibility);
    return()=>{
      observer.disconnect();query.removeEventListener('change',updateMotion);
      document.removeEventListener('visibilitychange',updateVisibility);
    };
  });
</script>

<div class="lab-brand" bind:this={root}>
  <a class="lab-brand-link" href="/" aria-label="Glimmer Lab home">
    <span class="brand-word" aria-hidden="true">
      {#if !reduced&&visible&&inView&&!paused}
        <SparklesText as="span" class="brand-sparkles" sparklesCount={4} {colors}>glimmer</SparklesText>
      {:else}
        glimmer
      {/if}
    </span>
    <span class="brand-tag" aria-hidden="true">LAB</span>
  </a>
  {#if !reduced}
    <button class="brand-motion-toggle" type="button" aria-label={paused?'Resume logo sparkles':'Pause logo sparkles'} title={paused?'Resume logo sparkles':'Pause logo sparkles'} onclick={()=>paused=!paused}>
      {#if paused}<Play size={12} aria-hidden="true"/>{:else}<Pause size={12} aria-hidden="true"/>{/if}
    </button>
  {/if}
</div>

<style>
  .lab-brand{display:flex;align-items:center;gap:4px;color:var(--color-ink);padding:0 10px 28px;flex-shrink:0}
  .lab-brand-link{display:inline-flex;align-items:center;gap:8px;min-width:0;color:inherit;white-space:nowrap;border-radius:4px}
  .brand-word{display:inline-block;font-size:24px;font-weight:550;letter-spacing:-1px;line-height:1.2}
  .brand-tag{font:10px var(--font-mono);letter-spacing:1px;color:var(--color-muted)}
  .brand-word :global(.brand-sparkles){display:inline;font:inherit;letter-spacing:inherit;line-height:inherit}
  .brand-word :global(.brand-sparkles strong){font-weight:inherit}
  .brand-word :global(.brand-sparkles svg){width:11px;height:11px;translate:-50% -50%;pointer-events:none}
  .brand-motion-toggle{display:inline-flex;align-items:center;justify-content:center;flex:none;width:24px;height:24px;border:0;border-radius:4px;padding:0;background:transparent;color:var(--color-muted)}
  .brand-motion-toggle:hover,.brand-motion-toggle:focus-visible{color:var(--color-ink);background:var(--color-paper-2)}
  @media(hover:hover){.brand-motion-toggle{opacity:0}.lab-brand:hover .brand-motion-toggle,.lab-brand:focus-within .brand-motion-toggle{opacity:1}}
</style>
