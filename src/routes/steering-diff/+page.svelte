<script lang="ts">
  import Explorer from '$lib/steering-diff/Explorer.svelte';
  import '$lib/steering-diff/style.css';
</script>

<svelte:head>
  <title>Steering diff | Glimmer Lab</title>
  <meta name="description" content="Recorded Baseline, Steered and Difference views. Precomputed comparisons, not live inference or a safety verdict."/>
</svelte:head>

<Explorer/>
