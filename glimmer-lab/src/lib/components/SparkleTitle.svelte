<script lang="ts">
  import {onMount} from 'svelte';
  import type {Snippet} from 'svelte';
  import {SparklesText} from '@nomos/svelte-magic/sparkles-text';

  let {children}:{children:Snippet}=$props();
  let reduced=$state(true),visible=$state(true),paused=$state(false);
  const colors={first:'var(--color-accent)',second:'var(--color-focus)'};

  onMount(()=>{
    const query=matchMedia('(prefers-reduced-motion: reduce)');
    const updateMotion=()=>reduced=query.matches;
    const updateVisibility=()=>visible=document.visibilityState==='visible';
    updateMotion();updateVisibility();
    query.addEventListener('change',updateMotion);
    document.addEventListener('visibilitychange',updateVisibility);
    return()=>{query.removeEventListener('change',updateMotion);document.removeEventListener('visibilitychange',updateVisibility);};
  });
</script>

<span class="title-sparkles">
  {#if reduced}
    {@render children()}
  {:else}
    <button class="title-sparkles-toggle" type="button" aria-label={`${paused?'Resume':'Pause'} sparkles for Muse Glimmer on Nomos`} title={paused?'Resume title sparkles':'Pause title sparkles'} onclick={()=>paused=!paused}>
      {#if visible&&!paused}
        <SparklesText as="span" class="title-sparkles-effect" sparklesCount={4} {colors} aria-hidden="true">{@render children()}</SparklesText>
      {:else}
        <span aria-hidden="true">{@render children()}</span>
      {/if}
    </button>
  {/if}
</span>

<style>
  .title-sparkles{display:inline-flex;position:relative}
  .title-sparkles-toggle{display:inline-flex;align-items:center;min-height:24px;border:0;padding:0;background:transparent;color:inherit;font:inherit;line-height:inherit;text-align:left;white-space:nowrap}
  .title-sparkles :global(.title-sparkles-effect){display:inline;font-size:inherit;font-weight:inherit;line-height:inherit}
  .title-sparkles :global(.title-sparkles-effect strong){font-weight:inherit}
  .title-sparkles :global(.title-sparkles-effect svg){width:11px;height:11px;translate:-50% -50%;pointer-events:none}
</style>
