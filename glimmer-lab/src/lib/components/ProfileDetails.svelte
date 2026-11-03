<script lang="ts">
  import {Dialog} from 'bits-ui';
  import {profileSize,renderedProfile,type ProfileRevision} from 'glimmer-agent/profile-model';
  let {profile,label='Inspect profile'}:{profile:ProfileRevision;label?:string}=$props();
  const size=$derived(profileSize(profile.content));
</script>
<Dialog.Root>
  <Dialog.Trigger class="profile-inspect">{label}</Dialog.Trigger>
  <Dialog.Portal><Dialog.Overlay class="profile-overlay"/><Dialog.Content class="profile-dialog">
    <Dialog.Title>Applied profile r{profile.revision}</Dialog.Title>
    <Dialog.Description>Recorded instructions and inline skills for this revision. Metadata is not sent to Glimmer. Filesystem state is not rewound.</Dialog.Description>
    <p>{size.characters.toLocaleString()} rendered characters · ~{size.approximateTokens.toLocaleString()} estimated tokens / {size.contextTokens.toLocaleString()} context. Character/4 heuristic, not tokenizer usage; conversation and response need space too.</p>
    <p>Tools: {profile.content.tools?.join(', ')||'none'} · Skills: inline · Policy: {profile.policy}</p><code>{profile.digest}</code>
    <pre>{renderedProfile(profile.content)}</pre>
    {#if profile.origin}<h3>Import provenance</h3><p>{profile.origin.delivery}</p><p>{profile.origin.edited?'Edited since import.':'Imported text snapshot.'} {profile.origin.source}</p><pre>{JSON.stringify(profile.origin,null,2)}</pre>{/if}
    <Dialog.Close class="profile-close">Close profile</Dialog.Close>
  </Dialog.Content></Dialog.Portal>
</Dialog.Root>
<style>
  :global(.profile-inspect){font-size:11px;color:var(--color-accent);background:transparent;border:1px solid var(--color-rule);border-radius:5px;padding:5px 8px;cursor:pointer}
  :global(.profile-overlay){position:fixed;inset:0;background:#0008;z-index:90}
  :global(.profile-dialog){position:fixed;z-index:91;top:50%;left:50%;transform:translate(-50%,-50%);width:min(850px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:26px;background:var(--color-paper);border:1px solid var(--color-rule);border-radius:12px;color:var(--color-ink);box-shadow:0 12px 60px #0004;font-size:13px;line-height:1.7}
  :global(.profile-dialog pre){white-space:pre-wrap;overflow-wrap:anywhere;background:var(--color-paper-2);padding:14px;font:11px/1.7 var(--font-mono);max-height:50vh;overflow:auto;margin:14px 0}
  :global(.profile-dialog code){font:10px var(--font-mono);overflow-wrap:anywhere}
  :global(.profile-close){padding:8px 16px;color:var(--color-ink);background:var(--color-paper-2);border:1px solid var(--color-rule);border-radius:6px;cursor:pointer}
</style>
