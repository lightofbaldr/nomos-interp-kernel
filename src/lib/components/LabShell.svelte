<script lang="ts">
  import {onMount,setContext,type Snippet} from 'svelte';
  import {page} from '$app/state';
  import {goto} from '$app/navigation';
  import {Dialog} from 'bits-ui';
  import {CircleNotch,ChatCircle,Graph,SlidersHorizontal,ArrowsSplit,Plus,ArrowRight,SidebarSimple,X,Archive,ChartLine,Eye,HardDrives} from 'phosphor-svelte';
  import LiveChat from './LiveChat.svelte';
  import LiveDock from './LiveDock.svelte';
  import LiveInspector from './LiveInspector.svelte';
  import {emptyLiveState,type LiveState} from '$lib/live';
  import {LAB_CONTEXT} from '$lib/workspace/context';
  import './lab-shell.css';
  let {children}:{children:Snippet}=$props();
  let live=$state.raw<LiveState>(emptyLiveState()),history=$state<{id:string;title?:string;busy:boolean}[]>([]);
  let controls=$state(false),navigation=$state(false),mounted=$state(false),historyError=$state('');
  const path=$derived(page.url.pathname),isChat=$derived(path==='/');
  const requested=$derived(page.url.searchParams.get('conversation')??'');
  let activate=$state(false);
  const title=$derived(live.conversations?.find(c=>c.id===live.conversation)?.title??'New conversation');
  setContext(LAB_CONTEXT,{get live(){return live;}});
  const link=(p:string)=>`${p}${live.conversation?`?conversation=${live.conversation}`:''}`;
  const links=[{href:'/',name:'Chat',icon:ChatCircle},{href:'/workspace',name:'Global Workspace',icon:Graph},{href:'/statistics',name:'Statistics',icon:ChartLine},{href:'/interpreter',name:'Interpreter',icon:Eye},{href:'/system',name:'System',icon:HardDrives},{href:'/workbench',name:'Harness workbench',icon:SlidersHorizontal}];
  function update(state:LiveState){
    live={...state,openConversation:async id=>{await state.openConversation?.(id);await goto(`${path}?conversation=${encodeURIComponent(id)}`);}};
    history=state.conversations??history;
  }
  let createRequested=$state(false);
  async function newChat(){
    const create=live.newConversation;
    if(!create){createRequested=true;activate=true;}
    await goto('/');if(create){await create();if(live.conversation)await goto(`/?conversation=${live.conversation}`,{replaceState:true});}navigation=false;
  }
  onMount(()=>{mounted=true;void fetch('/api/pi?history=1').then(r=>r.json()).then(d=>{if(d.conversations)history=d.conversations;else historyError=d.error??'History unavailable.';}).catch(()=>historyError='History unavailable.');});
  $effect(()=>{if(mounted&&(isChat||requested||['/workbench','/statistics','/interpreter'].includes(path)))activate=true;});
</script>

{#snippet sidebar()}
  <a class="lab-brand" href="/" aria-label="Glimmer Lab home"><CircleNotch size={29}/><span>glimmer<span>LAB</span></span></a>
  <button class="lab-new" onclick={newChat}><Plus size={17}/>New conversation</button>
  <nav aria-label="Lab pages">{#each links as l}<a href={link(l.href)} aria-current={path===l.href?'page':undefined} onclick={()=>navigation=false}><l.icon size={19}/>{l.name}</a>{/each}</nav>
  <div class="lab-history"><h2>Conversations <span>{history.length||''}</span></h2>
    {#if historyError}<p role="status">{historyError}</p>{/if}
    {#each history as c}<a class:chosen={c.id===live.conversation} href={`/?conversation=${c.id}`} title={c.title??`Conversation ${c.id}`} onclick={()=>navigation=false}><ChatCircle size={15}/><span>{c.title??`Conversation ${c.id}`}</span>{#if c.busy}<i aria-label="Generating"></i>{/if}</a>{/each}
    {#if !history.length&&!historyError}<p>Your conversations appear here when you start chatting. They stay saved after a reload.</p>{/if}
  </div>
  <div class="lab-sidebar-bottom"><a href="/workspace?recording=sietti-base"><Archive size={17}/>Recorded examples</a><a href="/steering-diff"><ArrowsSplit size={17}/>Steering diff</a><div class="lab-local"><span class:ready={live.health?.ready}></span>{live.health?.ready?'Glimmer connected':'Local workspace'}<small>Private · Nomos / Pi</small></div></div>
{/snippet}

<a href="#lab-content" class="skip-link">Skip to content</a>
<div class="video-shell">
  <aside class="lab-sidebar" aria-label="Conversations and navigation">{@render sidebar()}</aside>
  <div class="lab-body">
    <div class="lab-topline"><button class="icon-button lab-menu" onclick={()=>navigation=true} aria-label="Open navigation"><SidebarSimple size={22}/></button><span>Glimmer Lab <span class="lab-slash">/</span> {links.find(l=>l.href===path)?.name??'Workspace'}</span><span class="lab-top-meta">Weights unchanged. A closer look inside.</span></div>
    {#if isChat}<header class="lab-page-heading chat-heading"><div><span class="lab-eyebrow">CONVERSATION</span><h1>{title}</h1><p>Talk to Glimmer. Inspect the signals behind any saved turn.</p></div><button class="lab-button" onclick={()=>controls=true}><SlidersHorizontal size={17}/>Steering &amp; readouts</button></header>{/if}
    <main id="lab-content" class="lab-content" tabindex="-1">
      <div class="lab-chat chat-main" hidden={!isChat}>
        {#if activate}<LiveChat compact startNew={createRequested} requestedConversation={requested} onState={update}/>{:else}<p class="lab-empty">Opening your local workspace…</p>{/if}
        <div class="lab-live-strip"><LiveDock {live}/><a href={link('/workspace')}>Open Global Workspace <ArrowRight size={15}/></a></div>
      </div>
      {@render children()}
    </main>
  </div>
</div>
<Dialog.Root bind:open={controls}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="lab-controls-dialog"><div class="lab-dialog-head"><Dialog.Title>Steering &amp; readout capture</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close controls"><X size={20}/></Dialog.Close></div><Dialog.Description class="lab-dialog-desc">Changes apply to the next request. This turn keeps its recorded settings.</Dialog.Description><LiveInspector {live} initialTab="control"/></Dialog.Content></Dialog.Portal></Dialog.Root>
<Dialog.Root bind:open={navigation}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="lab-nav-dialog"><Dialog.Title class="sr-only">Lab navigation</Dialog.Title><Dialog.Description class="sr-only">Pages and saved conversations.</Dialog.Description><Dialog.Close class="icon-button" aria-label="Close navigation"><X size={20}/></Dialog.Close>{@render sidebar()}</Dialog.Content></Dialog.Portal></Dialog.Root>
