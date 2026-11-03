<script lang="ts">
  import '@fontsource-variable/geist';
  import '@fontsource-variable/geist-mono';
  import '../app.css';
  import type {Snippet} from 'svelte';
  import {page} from '$app/state';
  import LabShell from '$lib/components/LabShell.svelte';
  let {children}:{children:Snippet}=$props();
</script>
{#if page.url.pathname==='/preview'||page.url.pathname==='/steering-diff'}
  {@render children()}
{:else}
  <LabShell>{@render children()}</LabShell>
{/if}
