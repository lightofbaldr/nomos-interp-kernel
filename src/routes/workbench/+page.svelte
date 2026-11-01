<script lang="ts">
  import {labContext} from '$lib/workspace/context';
  import LiveInspector from '$lib/components/LiveInspector.svelte';
  const lab=labContext();
</script>
<svelte:head><title>Harness workbench · Glimmer Lab</title></svelte:head>
<div class="workbench-page"><header><h1>Harness workbench</h1><p>Configure instructions, skills and sandboxed tools for this conversation.</p><p>Conversation: {lab.live.conversations?.find(c=>c.id===lab.live.conversation)?.title??'Opening…'}</p></header><LiveInspector live={lab.live} initialTab="profile"/></div>
<style>.workbench-page{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;height:100%;padding:28px}.workbench-page header{margin-bottom:24px}.workbench-page h1{font-size:28px;letter-spacing:-1px}.workbench-page p{font-size:13px;color:var(--color-muted);margin-top:10px}</style>
