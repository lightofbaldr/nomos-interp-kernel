<script lang="ts">
  import {onDestroy,onMount,untrack} from 'svelte';
  import {Dialog} from 'bits-ui';
  import * as Message from '@nomos/svelte-ai-elements/message';
  import * as PromptInput from '@nomos/svelte-ai-elements/prompt-input';
  import type {Message as SubmittedMessage} from '@nomos/svelte-ai-elements/prompt-input';
  import {CircleNotch,Plus,ChatCircle,SidebarSimple,ArrowUpRight,ArrowUp,Square,Atom,Play,Copy,Check,X,Info,UploadSimple,Code,WifiSlash,Shield,ArrowRight,BookOpen,ArrowsSplit} from 'phosphor-svelte';
  import Inspector from '$lib/components/Inspector.svelte';
  import MotionLabel from '$lib/components/MotionLabel.svelte';
  import WelcomeMotion from '$lib/components/WelcomeMotion.svelte';
  import SparkleTitle from '$lib/components/SparkleTitle.svelte';
  import EmotionDock from '$lib/components/EmotionDock.svelte';
  import LiveChat from '$lib/components/LiveChat.svelte';
  import {emptyLiveState,type LiveState} from '$lib/live';
  import {scenarios,demoTrace,customDemo,type Scenario} from '$lib/demo';
  import {parseTraceText,answerAt,MAX_TRACE_BYTES,type Trace} from '$lib/trace';

  let {data}:{data:{liveConfigured:boolean;demo:boolean}}=$props();
  let liveView=$state<LiveState>(emptyLiveState());
  let liveChat:{reset:()=>void}|null=$state(null);
  let mode=$state<'demo'|'recorded'|'live'>(untrack(()=>data.liveConfigured&&!data.demo?'live':'demo'));
  let trace=$state<Trace|null>(untrack(()=>mode==='live'?null:demoTrace(scenarios[0])));
  let selected=$state(17),progress=$state(17),following=$state(true),playing=$state(false);
  let inspectionOpen=$state(true),drawerOpen=$state(false),infoOpen=$state(false),connectOpen=$state(false);
  let compact=$state(false);
  let text=$state(''),error=$state(''),copied=$state(false),importing=$state(false);
  let history=$state<Trace[]>([]);
  let timer:ReturnType<typeof setTimeout>|undefined;
  let copyTimer:ReturnType<typeof setTimeout>|undefined;
  let intentRevision=0;
  let conversation:HTMLElement|undefined=$state();
  let fileInput:HTMLInputElement;
  let textarea:HTMLTextAreaElement|null=$state(null);
  const visibleTrace=$derived(mode==='live'?null:trace);
  const frame=$derived(visibleTrace?.frames[selected]??null);
  const answer=$derived(visibleTrace?answerAt(visibleTrace,progress):'');
  const label=$derived(mode==='live'?(liveView.health?.ready?'Live kernel':'Live / disconnected'):mode==='recorded'?'Recorded trace':'Demo replay');
  const activeTitle=$derived(visibleTrace?.title??'New conversation');

  function stop(){if(timer)clearTimeout(timer);timer=undefined;playing=false;}
  function focusComposer(){requestAnimationFrame(()=>textarea?.focus());}
  function openTrace(next:Trace,nextMode:'demo'|'recorded',replay=false){
    stop();intentRevision++;importing=false;mode=nextMode;trace=next;error='';following=true;copied=false;
    progress=replay?0:next.frames.length-1;selected=progress;drawerOpen=false;
    if(replay){playing=true;advance();}
  }
  function advance(){
    if(!trace||!playing)return;
    timer=setTimeout(()=>{if(!trace||!playing)return;if(progress<trace.frames.length-1){progress++;if(following)selected=progress;advance();}else stop();},240);
  }
  function replay(){if(!trace||mode==='live')return;openTrace(trace,mode,true);}
  function newChat(){if(mode==='live'){liveChat?.reset();drawerOpen=false;return;}stop();intentRevision++;importing=false;trace=null;mode='demo';selected=0;progress=0;following=true;text='';error='';drawerOpen=false;focusComposer();}
  function choose(scenario:Scenario){openTrace(demoTrace(scenario),'demo',true);}
  function submit(message:SubmittedMessage){
    if(mode==='live'||playing)return;
    const prompt=message.text.trim();if(!prompt){error='Write a message before sending.';return;}
    if(prompt.length>4000){error='This preview accepts up to 4,000 characters. Shorten the message.';return;}
    if(trace)history=[trace,...history].slice(0,8);
    text='';openTrace(customDemo(prompt),'demo',true);focusComposer();
  }
  function select(index:number){if(!trace)return;selected=Math.max(0,Math.min(progress,index));following=false;}
  async function copyAnswer(){
    try{await navigator.clipboard.writeText(answer);copied=true;if(copyTimer)clearTimeout(copyTimer);copyTimer=setTimeout(()=>copied=false,2500);}
    catch{error='Clipboard access was denied. Select and copy the response text instead.';}
  }
  function download(value:string,name:string){const url=URL.createObjectURL(new Blob([value],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function exportTrace(){if(visibleTrace)download(JSON.stringify(visibleTrace,null,2),`${visibleTrace.runId.replace(/[^a-z0-9_-]/gi,'_')}.json`);}
  async function importFile(event:Event){
    const target=event.currentTarget as HTMLInputElement,file=target.files?.[0];target.value='';if(!file)return;
    if(file.size>MAX_TRACE_BYTES){error='Trace rejected: file exceeds 2 MB. Choose a smaller trace.';return;}
    stop();const revision=++intentRevision;importing=true;
    try{const next=parseTraceText(await file.text());if(revision!==intentRevision)return;openTrace(next,'recorded');inspectionOpen=true;}
    catch(e){if(revision===intentRevision)error=e instanceof Error?e.message:'The trace could not be read. Choose a valid JSON trace.';}
    finally{if(revision===intentRevision)importing=false;}
  }
  function disconnected(){stop();intentRevision++;importing=false;mode='live';error='';connectOpen=false;}
  onMount(()=>{const query=matchMedia('(max-width:59.99rem)');compact=query.matches;inspectionOpen=!compact;const change=()=>{compact=query.matches;inspectionOpen=!compact;};query.addEventListener('change',change);return()=>query.removeEventListener('change',change);});
  onDestroy(()=>{stop();if(copyTimer)clearTimeout(copyTimer);});
</script>

<svelte:head><title>Muse on Nomos | Chat with insight</title><meta name="description" content="A local chat-first interface for Muse, with read-only workspace and emotion-concept inspection. Demonstration data is explicitly labelled."/></svelte:head>
<a href="#conversation" class="skip-link">Skip to conversation</a>
<div class="app-shell" class:inspection-hidden={!inspectionOpen}>
  <aside class="history-sidebar" aria-label="Conversations">
    <a class="nomos-brand" href="/" aria-label="nomos MUSE home"><CircleNotch aria-hidden="true" size={27} weight="bold"/><span>nomos <span class="brand-sub">MUSE</span></span></a>
    <button class="new-chat" onclick={newChat}><Plus aria-hidden="true" size={19}/><span>New conversation</span></button>
    <div class="conversation-groups"><div class="sidebar-group"><span class="sidebar-label">Explore the demo</span>{#each scenarios as scenario}<button class="conversation-link" class:active={trace?.runId===`demo-${scenario.id}`&&mode==='demo'} onclick={()=>choose(scenario)}><ChatCircle aria-hidden="true" size={17}/><span>{scenario.title}</span></button>{/each}</div>
    {#if history.length}<div class="sidebar-group"><span class="sidebar-label">This session</span>{#each history as record,i}<button class="conversation-link" onclick={()=>openTrace(record,record.source==='demo'?'demo':'recorded')}><ChatCircle aria-hidden="true" size={16}/><span>{record.title}{i>0?` ${i+1}`:''}</span></button>{/each}</div>{/if}</div>
    <div class="sidebar-bottom"><a class="conversation-link" href="/steering-diff"><ArrowsSplit aria-hidden="true" size={18}/><span>Steering diff · recorded</span></a><button class="conversation-link" onclick={()=>fileInput.click()} disabled={importing}><UploadSimple aria-hidden="true" size={18}/><span>{importing?'Reading trace…':'Import trace'}</span></button><button class="conversation-link" onclick={()=>infoOpen=true}><BookOpen aria-hidden="true" size={18}/><span>About the readouts</span></button><button class="connection-card" onclick={()=>connectOpen=true}><span class="connection-icon">{#if liveView.health?.ready}<CircleNotch aria-hidden="true" size={18}/>{:else}<WifiSlash aria-hidden="true" size={18}/>{/if}</span><span><strong>{liveView.health?.ready?'Glimmer connected':'Kernel not connected'}</strong><small>{mode==='live'?'Live chat · readouts pending':'Local interface preview'}</small></span><ArrowUpRight aria-hidden="true" size={16}/></button><div class="account-row"><span class="account-avatar">A</span><div><strong>Adam’s workspace</strong><small>Private local workspace</small></div></div></div>
  </aside>

  <main class="chat-main">
    <header class="chat-header"><div class="model-heading"><button class="icon-button mobile-nav" onclick={()=>drawerOpen=true} aria-label="Open conversations"><SidebarSimple aria-hidden="true" size={22}/></button><div><SparkleTitle><span class="model-name">Muse Glimmer <span class="model-on">on Nomos</span></span></SparkleTitle><span class="model-caption">A conversation with a closer look.</span></div></div><div class="header-actions"><button class="source-button" aria-label={label} onclick={()=>connectOpen=true}><span class="status-ring" class:connected={mode!=='live'||liveView.health?.ready}></span><span class="source-label">{label}</span><span class="source-short">{mode==='demo'?'Demo':mode==='recorded'?'Trace':liveView.health?.ready?'Live':'Offline'}</span></button><button class="icon-button inspector-toggle" class:active={inspectionOpen} aria-label={inspectionOpen?'Hide inspection':'Show inspection'} aria-pressed={inspectionOpen} onclick={()=>inspectionOpen=!inspectionOpen}><Atom aria-hidden="true" size={21}/></button></div></header>
    <div class="demo-disclosure" role="status"><Info aria-hidden="true" size={15}/><span>{mode==='demo'?'Scripted chat and illustrative probe data. No model is running.':mode==='recorded'?'Imported record. Its model and probe metadata are not independently attested.':liveView.health?.ready?'Live Glimmer chat. Workspace and emotion readouts are not connected.':'Waiting for the configured Glimmer kernel. Readouts and steering are not connected.'}</span></div>
    {#if mode==='live'}
      <LiveChat bind:this={liveChat} onState={(state)=>liveView=state}/>
    {:else}
    <div class="conversation-stage">
      <img class="chat-mascot" src="/img/muse-mascot.webp" alt="" aria-hidden="true" width="1129" height="1393" decoding="async" fetchpriority="low"/>
    <div class="conversation-scroll" id="conversation" tabindex="-1" bind:this={conversation}>
      <div class="conversation-body">
        {#if visibleTrace}
          <div class="conversation-date">{activeTitle}</div>
          <Message.Root from="user" class="user-message"><Message.Content class="message-copy">{visibleTrace.messages[0].text}</Message.Content></Message.Root>
          <article class="assistant-message" aria-label={mode==='demo'?'Scripted assistant response':'Recorded assistant response'}>
            <div class="assistant-identity"><span class="muse-mark"><CircleNotch aria-hidden="true" size={25} weight="bold"/></span><strong>Muse</strong><span class="assistant-source">{mode==='demo'?'Scripted preview':'Recorded / unverified'}</span></div>
            <Message.Root from="assistant" class="assistant-body"><Message.Content class="message-copy"><div class="response-text" aria-busy={playing}>{#each answer.split('\n\n') as paragraph}<p>{paragraph}</p>{/each}{#if playing}<span class="stream-caret" aria-hidden="true"></span>{/if}</div></Message.Content></Message.Root>
            <div class="message-actions"><button class="icon-button" aria-label={copied?'Response copied':'Copy response'} onclick={copyAnswer}>{#if copied}<Check aria-hidden="true" size={18}/>{:else}<Copy aria-hidden="true" size={18}/>{/if}</button><button class="inspect-response" onclick={()=>inspectionOpen=true}><Atom aria-hidden="true" size={16}/>Inspect this response<ArrowUpRight aria-hidden="true" size={14}/></button></div>
            <div class="response-boundary"><Shield aria-hidden="true" size={16}/><span>Probe signals help investigate. They do not certify this answer.</span></div>
          </article>
          <div class="next-prompt"><span>Keep exploring</span><button onclick={()=>{text='Help me draft a redacted debugging packet.';focusComposer();}}>Draft a safer next step<ArrowUpRight aria-hidden="true" size={14}/></button></div>
        {:else}
          <div class="welcome"><WelcomeMotion><span class="welcome-mark"><CircleNotch aria-hidden="true" size={43} weight="bold"/></span><h1>Space to think.<br/>A way to look closer.</h1><p>Ask a question, or explore a sample conversation. Keep the response and its inspection signals in view.</p></WelcomeMotion><div class="welcome-prompts">{#each scenarios as scenario}<button onclick={()=>choose(scenario)}><span>{scenario.description}</span><ArrowUpRight aria-hidden="true" size={19}/></button>{/each}</div></div>
        {/if}
      </div>
    </div>
    </div>
    <section class="composer-section" aria-label="Message composer">
      <div class="replay-toolbar"><div class="replay-meta"><span class="replay-icon"><Play aria-hidden="true" size={13} weight="fill"/></span><span>{playing?'Playing scripted response':mode==='recorded'?'Recorded response':'Try the conversation'}</span></div>{#if visibleTrace}<button class="text-button" onclick={playing?stop:replay}>{#if playing}<Square aria-hidden="true" size={12} weight="fill"/>Stop replay{:else}<Play aria-hidden="true" size={13} weight="fill"/><MotionLabel label="Replay response"/>{/if}</button>{/if}</div>
      <PromptInput.Root class="muse-composer" onSubmit={submit} clearOnSubmit={false} maxFiles={0} maxFileSize={0} onError={()=>error='Attachments are not enabled in this local preview.'}>
        <PromptInput.Body><label class="sr-only" for="message-input">Message Muse</label><PromptInput.Textarea id="message-input" bind:ref={textarea} bind:value={text} placeholder="Message Muse…" maxlength={4000} aria-describedby="composer-helper"/></PromptInput.Body>
        <PromptInput.Toolbar class="composer-toolbar"><div class="composer-tools"><span><CircleNotch aria-hidden="true" size={15}/>Muse Glimmer</span><span class="composer-thinking">Local preview</span></div><PromptInput.Submit class="send-button" status={playing?'streaming':'ready'} onStop={stop} disabled={!playing&&!text.trim()} aria-label={playing?'Stop replay':'Send local preview'}>{#if playing}<Square aria-hidden="true" size={15} weight="fill"/>{:else}<ArrowUp aria-hidden="true" size={20} weight="bold"/>{/if}</PromptInput.Submit></PromptInput.Toolbar>
      </PromptInput.Root>
      {#if error}<p class="error-message" role="alert"><Info aria-hidden="true" size={16}/>{error}<button class="icon-button" aria-label="Dismiss error" onclick={()=>error=''}><X aria-hidden="true" size={16}/></button></p>{/if}
      <p class="composer-helper" id="composer-helper">{mode==='demo'?'Local demo only. Enter sends; Shift + Enter adds a line.':mode==='recorded'?'This is a recorded trace. Sending starts a new, explicitly scripted preview.':'No model connected. Enable a real transport before relying on responses.'}</p>
    </section>
    {/if}
    <div class="steering-footer"><EmotionDock/></div>
  </main>

  {#if inspectionOpen&&!compact}<aside class="inspection-panel" aria-label="Response inspection"><Inspector trace={visibleTrace} {frame} {selected} available={progress} {following} {mode} live={liveView} onFreeze={()=>following=false} onFollow={()=>{following=true;selected=progress;}} onSelect={select} onInfo={()=>infoOpen=true} onExport={exportTrace} onClose={()=>inspectionOpen=false}/></aside>{/if}
</div>

{#if compact}<Dialog.Root bind:open={inspectionOpen}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="mobile-inspection"><Dialog.Title class="sr-only">Response inspection</Dialog.Title><Dialog.Description class="sr-only">Read-only workspace and emotion-concept readouts for the selected generation position.</Dialog.Description><Inspector trace={visibleTrace} {frame} {selected} available={progress} {following} {mode} live={liveView} onFreeze={()=>following=false} onFollow={()=>{following=true;selected=progress;}} onSelect={select} onInfo={()=>infoOpen=true} onExport={exportTrace} onClose={()=>inspectionOpen=false}/></Dialog.Content></Dialog.Portal></Dialog.Root>{/if}

<input class="sr-only" tabindex="-1" bind:this={fileInput} type="file" accept="application/json,.json" aria-label="Import recorded trace" onchange={importFile}/>
<Dialog.Root bind:open={drawerOpen}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="modal-panel navigation-dialog"><div class="modal-title"><Dialog.Title>Conversations</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close conversations"><X aria-hidden="true" size={21}/></Dialog.Close></div><Dialog.Description class="modal-description">Demo conversations and this local session.</Dialog.Description><a class="modal-action" href="/steering-diff"><ArrowsSplit aria-hidden="true" size={18}/>Steering diff · recorded</a><button class="modal-action" onclick={newChat}><Plus aria-hidden="true" size={18}/>New conversation</button>{#each scenarios as scenario}<button class="modal-action" onclick={()=>choose(scenario)}><ChatCircle aria-hidden="true" size={18}/>{scenario.title}</button>{/each}<button class="modal-action" onclick={()=>{drawerOpen=false;fileInput.click();}}><UploadSimple aria-hidden="true" size={18}/>Import trace</button></Dialog.Content></Dialog.Portal></Dialog.Root>
<Dialog.Root bind:open={infoOpen}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="modal-panel research-dialog"><div class="modal-title"><Dialog.Title>A closer look, not a verdict</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close readout information"><X aria-hidden="true" size={21}/></Dialog.Close></div><Dialog.Description class="modal-description">Read-only inspection for investigating a response. Not a safety classifier.</Dialog.Description><div class="research-sections"><section><h3>Global workspace</h3><p>A lens can surface concept representations at a selected layer and generation position. A lens readout is not the model’s complete reasoning or proof of conscious access.</p><a href="https://transformer-circuits.pub/2026/workspace/index.html" target="_blank" rel="noreferrer">Read the workspace research<ArrowUpRight aria-hidden="true" size={15}/></a></section><section><h3>Emotion concepts</h3><p>The research studies emotion-concept representations and their functional effects. A projection score is not a probability, and it does not establish that a model feels an emotion.</p><a href="https://transformer-circuits.pub/2026/emotions/index.html" target="_blank" rel="noreferrer">Read the emotion-concepts research<ArrowUpRight aria-hidden="true" size={15}/></a></section><section><h3>What this preview establishes</h3><p>The UI and trace controls work with explicit fixtures. We have not measured Muse’s workspace, calibrated emotion probes, or validated a safety decision from these signals. Missing metadata stays unknown.</p></section></div></Dialog.Content></Dialog.Portal></Dialog.Root>
<Dialog.Root bind:open={connectOpen}><Dialog.Portal><Dialog.Overlay class="modal-overlay"/><Dialog.Content class="modal-panel"><div class="modal-title"><Dialog.Title>Choose the data source</Dialog.Title><Dialog.Close class="icon-button" aria-label="Close data source"><X aria-hidden="true" size={21}/></Dialog.Close></div><Dialog.Description class="modal-description">Live chat uses the server-configured kernel. Demo and recorded readouts stay separate. No model is started by this interface.</Dialog.Description><div class="source-options"><button class="source-option" onclick={()=>{connectOpen=false;openTrace(demoTrace(scenarios[0]),'demo');}}><Play aria-hidden="true" size={20}/><span><strong>Demo replay</strong><small>Scripted answer and illustrative scores</small></span><ArrowRight aria-hidden="true" size={17}/></button><button class="source-option" onclick={()=>{connectOpen=false;fileInput.click();}}><UploadSimple aria-hidden="true" size={20}/><span><strong>Import a recorded trace</strong><small>Client-only JSON, up to 2 MB</small></span><ArrowRight aria-hidden="true" size={17}/></button><button class="source-option" onclick={disconnected}><CircleNotch aria-hidden="true" size={20}/><span><strong>Live kernel</strong><small>Stream real chat; unconnected probes stay empty</small></span><ArrowRight aria-hidden="true" size={17}/></button></div><p class="integration-note"><Code aria-hidden="true" size={17}/>Chat and KV counters use the serving API. Workspace, emotion and steering contracts remain pending.</p></Dialog.Content></Dialog.Portal></Dialog.Root>
