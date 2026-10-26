<script lang="ts">
  import '@fontsource-variable/geist';
  import '@fontsource-variable/geist-mono';
  import '../app.css';
  import type {Snippet} from 'svelte';
  let {children}:{children:Snippet}=$props();
</script>
{@render children()}
