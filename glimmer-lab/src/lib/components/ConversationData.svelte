<script lang="ts">
  import {Dialog} from 'bits-ui';
  import {Trash,X,Warning} from 'phosphor-svelte';
  type Target={scope:'all'}|{scope:'conversation';conversation:string};
  type Plan={target:Target;revision:string;ids:string[];conversations:number;entries:number;documents:number;confirmation:string};
  let {conversations,current='',busy=false}:{conversations:{id:string;title?:string;busy:boolean}[];current?:string;busy?:boolean}=$props();
  let open=$state(false),selected=$state(''),plan=$state<Plan|null>(null),confirmation=$state(''),pending=$state(false),error=$state(''),notice=$state('');
  const uid=$props.id();
  const blocked=$derived(busy||conversations.some(c=>c.busy));
  async function request(body:unknown){
    const r=await fetch('/api/pi/data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),data=await r.json();
    if(!r.ok)throw Error(data.error??'Deletion failed.');return data;
  }
  function begin(){selected=current||conversations[0]?.id||'';plan=null;confirmation='';error='';open=true;}
  async function preview(target:Target){
    pending=true;error='';confirmation='';
    try{plan=await request({action:'preview',target});}catch(e){error=e instanceof Error?e.message:'Cannot prepare deletion.';}finally{pending=false;}
  }
  async function purge(){
    if(!plan||pending||confirmation!==plan.confirmation)return;
    pending=true;error='';
    try{
      const result=await request({action:'purge',target:plan.target,revision:plan.revision,confirmation});
      notice=`Deleted ${result.conversations} conversation${result.conversations===1?'':'s'} and their saved data.${result.warning?' '+result.warning:''}`;
      // No transcript in browser storage. Other tabs refresh their history and
      // discard an open deleted conversation; the server also closes old bridges.
      if(plan.target.scope==='all')localStorage.setItem('glimmer.pi.empty','true');
      if(result.deleted.includes(localStorage.getItem('glimmer.pi.conversation')))localStorage.removeItem('glimmer.pi.conversation');
      localStorage.setItem('glimmer.pi.purge',String(Date.now()));
      window.dispatchEvent(new Event('glimmer-data-purged'));
      open=false;plan=null;confirmation='';
    }catch(e){error=e instanceof Error?e.message:'Deletion did not complete.';plan=null;confirmation='';}finally{pending=false;}
  }
</script>

<Dialog.Root bind:open onOpenChange={value=>{if(!value){plan=null;confirmation='';}}}>
  <Dialog.Trigger class="conversation-data-trigger" onclick={begin}><Trash size={17}/>Manage conversation data</Dialog.Trigger>
  {#if notice}<p class="data-notice" role="status">{notice}</p>{/if}
  <Dialog.Portal><Dialog.Overlay class="conversation-data-overlay"/>
    <Dialog.Content class="conversation-data-dialog" onInteractOutside={e=>{if(pending)e.preventDefault();}} onEscapeKeydown={e=>{if(pending)e.preventDefault();}}>
      <div class="data-heading"><Dialog.Title>Conversation data</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close conversation data" disabled={pending}><X size={20}/></Dialog.Close></div>
      <Dialog.Description class="data-description">Delete saved conversations from this Glimmer Lab server. This cannot be undone in the app.</Dialog.Description>
      <div class="data-scope"><strong>Removed together</strong><p>Chat and reasoning text, emotion and workspace readouts, second-opinion notes, conversation settings and profiles, tool results, and files created in that conversation’s sandbox.</p>
        <strong>Kept</strong><p>Recorded examples, steering-diff datasets, model assets, original imported harnesses, and external logs or backups. This does not clear the kernel’s KV cache or erase data already sent to a provider. It is not secure disk erasure.</p></div>
      {#if blocked}<p class="data-warning" role="status"><Warning size={18}/>Wait until generation and tool work have finished, or stop the conversation first.</p>{/if}
      {#if plan}
        <section class="data-confirm" aria-label="Deletion confirmation">
          <h2>{plan.target.scope==='all'?'Purge all conversations?':'Delete this conversation?'}</h2>
          <p>{plan.conversations} conversation{plan.conversations===1?'':'s'} · {plan.entries} saved entries · {plan.documents} settings/state records, plus their sandbox files.</p>
          {#if plan.target.scope==='conversation'}<p class="data-name">{conversations.find(c=>c.id===plan?.ids[0])?.title??`Conversation ${plan.ids[0]}`}</p>{/if}
          <label for={`${uid}-confirmation`}>Type <strong>{plan.confirmation}</strong> to confirm</label>
          <input id={`${uid}-confirmation`} bind:value={confirmation} autocomplete="off" spellcheck="false" disabled={pending}/>
          <div class="data-actions"><button class="lab-button" onclick={()=>{plan=null;confirmation='';}} disabled={pending}>Cancel</button><button class="data-danger" onclick={purge} disabled={pending||blocked||confirmation!==plan.confirmation}>{pending?'Deleting…':plan.target.scope==='all'?'Permanently purge all':'Permanently delete'}</button></div>
        </section>
      {:else}
        <label class="data-selection" for={`${uid}-conversation`}>Choose a conversation<select id={`${uid}-conversation`} bind:value={selected} disabled={pending||!conversations.length}><option value="" disabled>Select a conversation</option>{#each conversations as c}<option value={c.id}>{c.title??`Conversation ${c.id}`} · #{c.id}</option>{/each}</select></label>
        <div class="data-actions"><button class="lab-button" onclick={()=>preview({scope:'conversation',conversation:selected})} disabled={!selected||pending||blocked}>Delete selected…</button><button class="data-danger" onclick={()=>preview({scope:'all'})} disabled={pending||blocked}>Purge all…</button></div>
        {#if !conversations.length}<p class="data-description">No saved conversations. Purge all also removes any leftover conversation sandbox files.</p>{/if}
      {/if}
      {#if error}<p role="alert" class="data-error">{error}</p>{/if}
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  :global(.conversation-data-trigger){display:flex;align-items:center;gap:9px;width:100%;text-align:left;padding:10px 0;color:var(--color-muted);font-size:12px}:global(.conversation-data-trigger:hover){color:var(--color-ink)}.data-notice{font-size:11px;line-height:1.5;padding:6px 0;color:var(--color-muted)}
  :global(.conversation-data-overlay){position:fixed;inset:0;background:var(--color-overlay);z-index:calc(var(--z-modal) + 1)}
  :global(.conversation-data-dialog){position:fixed;z-index:calc(var(--z-modal) + 2);left:50%;top:50%;transform:translate(-50%,-50%);width:min(560px,calc(100vw - 28px));max-height:calc(100dvh - 40px);overflow:auto;background:var(--color-paper);color:var(--color-ink);border:1px solid var(--color-rule);border-radius:12px;padding:26px;box-shadow:0 24px 90px #0003}
  .data-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:22px;font-weight:600}.data-description{font-size:13px;line-height:1.6;color:var(--color-muted);margin:12px 0}
  .data-scope{padding:16px 0;border-top:1px solid var(--color-rule);border-bottom:1px solid var(--color-rule);font-size:12px;line-height:1.6}.data-scope p{color:var(--color-muted);margin:4px 0 12px}.data-scope p:last-child{margin-bottom:0}
  .data-selection{display:grid;gap:8px;font-size:12px;margin-top:20px}.data-selection select,.data-confirm input{width:100%;border:1px solid var(--color-rule);background:var(--color-paper-2);color:var(--color-ink);border-radius:6px;padding:10px;min-height:42px}.data-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px;flex-wrap:wrap}
  .data-danger{background:#892d2b;color:#fff;border:1px solid #892d2b;padding:10px 14px;border-radius:6px;min-height:42px;font-size:12px;font-weight:600}.data-danger:disabled{opacity:.45;cursor:not-allowed}
  .data-confirm{margin-top:20px}.data-confirm h2{font-size:19px;margin-bottom:10px}.data-confirm p{font-size:12px;line-height:1.6;color:var(--color-muted);margin:10px 0}.data-confirm .data-name{color:var(--color-ink);font-weight:600;overflow-wrap:anywhere}.data-confirm label{display:block;font-size:12px;margin:16px 0 8px}.data-warning{display:flex;gap:8px;font-size:12px;line-height:1.6;margin-top:14px}.data-warning :global(svg){flex:none}.data-error{font-size:12px;line-height:1.6;color:#892d2b;margin-top:16px}
</style>
