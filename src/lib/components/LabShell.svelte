<script lang="ts">
  import {onMount,setContext,tick,type Snippet} from 'svelte';
  import {page} from '$app/state';
  import {goto} from '$app/navigation';
  import {Dialog} from 'bits-ui';
  import {ChatCircle,Graph,SlidersHorizontal,ArrowsSplit,Plus,ArrowRight,SidebarSimple,X,Archive,ChartLine,Eye,HardDrives} from 'phosphor-svelte';
  import LiveChat from './LiveChat.svelte';
  import LiveDock from './LiveDock.svelte';
  import LiveInspector from './LiveInspector.svelte';
  import ConversationData from './ConversationData.svelte';
  import SparkleBrand from './SparkleBrand.svelte';
  import {emptyLiveState,type LiveState} from '$lib/live';
  import {LAB_CONTEXT} from '$lib/workspace/context';
  import './lab-shell.css';
  let {children}:{children:Snippet}=$props();
  let live=$state.raw<LiveState>(emptyLiveState()),history=$state<{id:string;title?:string;busy:boolean}[]>([]);
  let controls=$state(false),navigation=$state(false),mounted=$state(false),historyError=$state('');
  let brandPaused=$state(false);
  let controlsTrigger:HTMLButtonElement|null=null,navTrigger:HTMLButtonElement;
  let historyAttempted=$state(false);
  const path=$derived(page.url.pathname),isChat=$derived(path==='/');
  const isRecordedHarness=$derived(path==='/workbench/recorded');
  const requested=$derived(page.url.searchParams.get('conversation')??'');
  let activate=$state(false);
  setContext(LAB_CONTEXT,{get live(){return live;}});
  const link=(p:string)=>`${p}${live.conversation?`?conversation=${live.conversation}`:''}`;
  const links=[{href:'/',name:'Chat',icon:ChatCircle},{href:'/workspace',name:'Global Workspace',icon:Graph},{href:'/statistics',name:'Statistics',icon:ChartLine},{href:'/interpreter',name:'Interpreter',icon:Eye},{href:'/system',name:'System',icon:HardDrives},{href:'/workbench',name:'Harness workbench',icon:SlidersHorizontal}];
  function update(state:LiveState){
    live={...state,openConversation:async id=>{await state.openConversation?.(id);await goto(`${path}?conversation=${encodeURIComponent(id)}`);}};
    history=state.conversations??history;
  }
  async function openControls(event:MouseEvent){
    controlsTrigger=event.currentTarget as HTMLButtonElement;
    navigation=false;
    // Let the mobile navigation close before the settings dialog takes focus.
    await tick();controls=true;
  }
  function restoreControlsFocus(event:Event){
    event.preventDefault();
    (controlsTrigger?.isConnected?controlsTrigger:navTrigger)?.focus();
  }
  let createRequested=$state(false);
  async function newChat(){
    const create=live.newConversation;
    if(!create){createRequested=true;activate=true;}
    await goto('/');if(create){await create();if(live.conversation)await goto(`/?conversation=${live.conversation}`,{replaceState:true});}navigation=false;
  }
  async function refreshHistory(){
    if(isRecordedHarness)return;
    // Claim the initial read synchronously, before awaiting it. Empty history
    // and failed reads must not cause a reactive fetch loop.
    historyAttempted=true;
    try{
      const r=await fetch('/api/pi?history=1'),d=await r.json();
      if(!r.ok||!d.conversations)throw Error(d.error??'History unavailable.');
      history=d.conversations;historyError='';
      if(requested&&!history.some(c=>c.id===requested)){const url=new URL(page.url);url.searchParams.delete('conversation');url.searchParams.delete('turn');await goto(url.pathname+url.search,{replaceState:true});}
    }catch(e){historyError=e instanceof Error?e.message:'History unavailable.';}
  }
  onMount(()=>{
    mounted=true;
    const storage=(e:StorageEvent)=>{if(e.key==='glimmer.pi.purge')void refreshHistory();};
    window.addEventListener('glimmer-data-purged',refreshHistory);window.addEventListener('storage',storage);
    return()=>{window.removeEventListener('glimmer-data-purged',refreshHistory);window.removeEventListener('storage',storage);};
  });
  // A direct recorded-page entry deliberately skips history. Read it once on
  // departure, including destinations that never mount LiveChat.
  $effect(()=>{if(mounted&&!isRecordedHarness&&!historyAttempted)void refreshHistory();});
  $effect(()=>{if(mounted&&!isRecordedHarness&&(isChat||requested||['/workbench','/statistics','/interpreter'].includes(path)))activate=true;});
</script>

{#snippet sidebar()}
  <SparkleBrand bind:paused={brandPaused}/>
  <button class="lab-new" onclick={newChat}><Plus size={17}/>New conversation</button>
  <nav aria-label="Lab pages">{#each links as l}<a href={link(l.href)} aria-current={path===l.href||(isRecordedHarness&&l.href==='/workbench')?'page':undefined} onclick={()=>navigation=false}><l.icon size={19}/>{l.name}</a>{/each}</nav>
  <div class="lab-history"><h2>Conversations <span>{history.length||''}</span></h2>
    {#if historyError}<p role="status">{historyError}</p>{/if}
    {#each history as c}<a class:chosen={c.id===live.conversation} href={`/?conversation=${c.id}`} title={c.title??`Conversation ${c.id}`} onclick={()=>navigation=false}><ChatCircle size={15}/><span>{c.title??`Conversation ${c.id}`}</span>{#if c.busy}<i aria-label="Generating"></i>{/if}</a>{/each}
    {#if !history.length&&!historyError}<p>{isRecordedHarness?'History is not loaded in this read-only view.':'Your conversations appear here when you start chatting. They stay saved after a reload.'}</p>{/if}
  </div>
  <div class="lab-sidebar-bottom">{#if !isRecordedHarness}<ConversationData conversations={history} current={live.conversation} busy={live.busy}/>{/if}<a href="/workbench/recorded"><Archive size={17}/>Recorded harness</a><a href="/workspace?recording=sietti-base"><Archive size={17}/>Recorded examples</a><a href="/steering-diff"><ArrowsSplit size={17}/>Steering diff</a>{#if !isRecordedHarness}<button class="lab-settings" onclick={openControls} aria-haspopup="dialog" aria-expanded={controls}><SlidersHorizontal size={17} aria-hidden="true"/>Steering &amp; readouts</button>{/if}<div class="lab-local"><span class:ready={!isRecordedHarness&&live.health?.ready}></span>{isRecordedHarness?'Read-only recording':live.health?.ready?'Glimmer connected':'Local workspace'}<small>Private · Nomos / Pi</small></div></div>
{/snippet}

<a href="#lab-content" class="skip-link">Skip to content</a>
<div class="video-shell">
  <aside class="lab-sidebar" aria-label="Conversations and navigation">{@render sidebar()}</aside>
  <div class="lab-body">
    <div class="lab-topline"><button class="icon-button lab-menu" bind:this={navTrigger} onclick={()=>navigation=true} aria-label="Open navigation"><SidebarSimple size={22}/></button><span>Glimmer Lab <span class="lab-slash">/</span> {isRecordedHarness?'Recorded harness comparison':links.find(l=>l.href===path)?.name??'Workspace'}</span><span class="lab-top-meta">{isRecordedHarness?'Recorded run · No live generation':'Weights unchanged. A closer look inside.'}</span></div>
    <main id="lab-content" class="lab-content" tabindex="-1">
      <div class="lab-chat chat-main" hidden={!isChat}>
        {#if activate&&!isRecordedHarness}<LiveChat compact startNew={createRequested} requestedConversation={requested} onState={update}/>{:else}<p class="lab-empty">Opening your local workspace…</p>{/if}
        <div class="lab-live-strip"><LiveDock {live}/><a href={link('/workspace')}>Open Global Workspace <ArrowRight size={15}/></a></div>
      </div>
      {@render children()}
    </main>
  </div>
</div>
<Dialog.Root bind:open={controls}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="lab-controls-dialog" onCloseAutoFocus={restoreControlsFocus}><div class="lab-dialog-head"><Dialog.Title>Steering &amp; readout capture</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close controls"><X size={20}/></Dialog.Close></div><Dialog.Description class="lab-dialog-desc">Changes apply to the next request. This turn keeps its recorded settings.</Dialog.Description><LiveInspector {live} initialTab="control"/></Dialog.Content></Dialog.Portal></Dialog.Root>
<Dialog.Root bind:open={navigation}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="lab-nav-dialog"><Dialog.Title class="sr-only">Lab navigation</Dialog.Title><Dialog.Description class="sr-only">Pages and saved conversations.</Dialog.Description><Dialog.Close class="icon-button" aria-label="Close navigation"><X size={20}/></Dialog.Close>{@render sidebar()}</Dialog.Content></Dialog.Portal></Dialog.Root>
