<script lang="ts">
  import {onMount} from 'svelte';
  import {Collapsible} from 'bits-ui';
  import * as Message from '@nomos/svelte-ai-elements/message';
  import * as PromptInput from '@nomos/svelte-ai-elements/prompt-input';
  import * as Reasoning from '@nomos/svelte-ai-elements/reasoning';
  import type {Message as SubmittedMessage} from '@nomos/svelte-ai-elements/prompt-input';
  import {CircleNotch,ArrowUp,Square,Brain,CaretDown,Copy,Check,Info,ArrowClockwise} from 'phosphor-svelte';
  import {consumeChat,emptyLiveState,readStats,SYSTEM_MESSAGE,type LiveState,type ChatMessage} from '$lib/live';

  let {onState}:{onState:(state:LiveState)=>void}=$props();
  type Entry=ChatMessage&{id:number;status?:'streaming'|'stop'|'length'|'stopped'|'error';error?:string;copied?:boolean};
  let view=$state<LiveState>(emptyLiveState()),messages=$state<Entry[]>([]),text=$state(''),error=$state('');
  let textarea:HTMLTextAreaElement|null=$state(null),scroll:HTMLDivElement;
  let controller:AbortController|undefined,alive=true,revision=0,nextId=0,follow=true,scrollFrame=0;
  const pending=new Set<AbortController>();let statsFlight:Promise<void>|null=null;
  function update(next:Partial<LiveState>){if(!alive)return;view={...view,...next};onState(view);}
  function latest(){if(!follow)return;cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(()=>{if(scroll)scroll.scrollTop=scroll.scrollHeight;});}
  async function get(path:string){const c=new AbortController();pending.add(c);try{const response=await fetch(path,{signal:c.signal});if(!response.ok)throw Error('Service unavailable.');return await response.json();}finally{pending.delete(c);}}
  async function refreshStats(){
    if(statsFlight)return statsFlight;
    if(!view.health?.ready||view.health.store!=='open'){update({stats:null,statsAt:null});return;}
    statsFlight=(async()=>{try{const data=await get('/api/nomos/kvstore');update({stats:data.stats?readStats(data.stats):null,statsAt:data.at,statsError:data.error??''});}catch{update({stats:null,statsAt:null,statsError:'KV statistics unavailable. Chat still works.'});}})();
    try{await statsFlight;}finally{statsFlight=null;}
  }
  async function refresh(){
    if(view.busy)return;
    try{const data=await get('/api/nomos/health');update({health:data.health,error:data.error??''});if(!view.busy)await refreshStats();}catch{update({health:null,stats:null,error:'Cannot reach the chat service. Check the connection.'});}
  }
  export function reset(){revision++;controller?.abort();messages=[];text='';error='';follow=true;update({busy:false,finish:null,firstTextMs:null,elapsedMs:null});}
  async function submit(message:SubmittedMessage){
    if(view.busy||!view.health?.ready)return;
    if(!message.text.trim())return;
    if(message.text.length>4000){error='Messages are limited to 4,000 characters.';return;}
    if(messages.length>=62){error='This conversation reached the interface message limit. Start a new conversation.';return;}
    const run=++revision;error='';update({busy:true,finish:null,firstTextMs:null,elapsedMs:null});
    // Read counters before opening generation, never by polling the locked
    // stats endpoint during the stream. This also waits for any initial read.
    controller=new AbortController();const active=controller;
    await refreshStats();if(run!==revision||!alive)return;
    if(active.signal.aborted){controller=undefined;update({busy:false,finish:'stopped'});return;}
    messages=[...messages,{id:++nextId,role:'user',content:message.text}];text='';
    const history:ChatMessage[]=[{role:'system',content:SYSTEM_MESSAGE},...messages.map(m=>({role:m.role,content:m.content,...(m.role==='assistant'?{reasoning_content:m.reasoning_content??''}:{})}))];
    const replyId=++nextId;messages=[...messages,{id:replyId,role:'assistant',content:'',reasoning_content:'',status:'streaming'}];
    const current=()=>messages.find(m=>m.id===replyId);const start=performance.now();follow=true;latest();
    try{
      const response=await fetch('/api/nomos/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:history}),signal:active.signal});
      const finish=await consumeChat(response,(field,delta)=>{
        if(!alive||run!==revision)return;
        const reply=current();if(!reply)return;reply[field]=(reply[field]??'')+delta;
        if(delta&&view.firstTextMs===null)update({firstTextMs:Math.round(performance.now()-start)});
        latest();
      });
      if(run!==revision||!alive)return;
      const reply=current();if(reply)reply.status=finish==='stop'?'stop':finish==='length'?'length':'error';
      update({finish});if(finish==='length')error='The reply reached its output limit. Partial reasoning and answer are preserved.';
      else if(finish!=='stop')error=`The kernel ended this reply with ${finish}.`;
    }catch(e){
      if(run!==revision||!alive)return;
      const stopped=active.signal.aborted,reply=current();
      if(reply){reply.status=stopped?'stopped':'error';reply.error=stopped?'Stopped by you. Partial text is kept in the conversation.':e instanceof Error?e.message:'The chat stream failed.';}
      error=stopped?'':reply?.error??'The chat stream failed.';update({finish:stopped?'stopped':'error'});
    }finally{
      if(run===revision&&alive){controller=undefined;update({busy:false,elapsedMs:Math.round(performance.now()-start)});latest();await refreshStats();}
    }
  }
  async function copy(entry:Entry){try{await navigator.clipboard.writeText(entry.content);entry.copied=true;}catch{error='Clipboard unavailable. Select the answer text to copy it.';}}
  onMount(()=>{
    alive=true;void refresh();const timer=setInterval(()=>{if(document.visibilityState==='visible'&&!view.busy)void refresh();},15000);
    return()=>{alive=false;revision++;clearInterval(timer);controller?.abort();for(const c of pending)c.abort();cancelAnimationFrame(scrollFrame);};
  });
</script>

<div class="conversation-stage">
  <img class="chat-mascot" src="/img/muse-mascot.webp" alt="" aria-hidden="true" width="1129" height="1393" decoding="async" fetchpriority="low"/>
  <div class="conversation-scroll" id="conversation" tabindex="-1" bind:this={scroll} onscroll={()=>follow=scroll.scrollHeight-scroll.clientHeight-scroll.scrollTop<80}>
    <div class="conversation-body live-conversation" aria-label="Live conversation">
      {#if !messages.length}
        <div class="welcome live-welcome"><span class="welcome-mark"><CircleNotch aria-hidden="true" size={43} weight="bold"/></span><h1>Space to think.<br/>A way to look closer.</h1><p>{view.health?.ready?'Glimmer is ready. What would you like to explore?':'Connect to your Glimmer workstation to start a conversation.'}</p></div>
      {/if}
      {#each messages as entry (entry.id)}
        {#if entry.role==='user'}
          <Message.Root from="user" class="user-message live-user"><Message.Content class="message-copy"><div class="live-plain">{entry.content}</div></Message.Content></Message.Root>
        {:else}
          <article class="assistant-message live-assistant" aria-label="Live assistant response" data-status={entry.status}>
            <div class="assistant-identity"><span class="muse-mark"><CircleNotch aria-hidden="true" size={25} weight="bold"/></span><strong>Muse</strong><span class="assistant-source">Live kernel</span></div>
            {#if entry.reasoning_content}
              <Reasoning.Root class="live-reasoning" defaultOpen={false} isStreaming={entry.status==='streaming'&&!entry.content}>
                <Reasoning.Trigger class="live-reasoning-trigger"><Brain aria-hidden="true" size={17}/><span>{entry.status==='streaming'&&!entry.content?'Reasoning…':'Show reasoning'}</span><CaretDown aria-hidden="true" size={14}/></Reasoning.Trigger>
                <Collapsible.Content class="live-reasoning-content"><div class="live-plain reasoning-text">{entry.reasoning_content}</div></Collapsible.Content>
              </Reasoning.Root>
            {/if}
            <Message.Root from="assistant" class="assistant-body"><Message.Content class="message-copy"><div class="response-text live-plain" aria-busy={entry.status==='streaming'}>{entry.content}</div></Message.Content></Message.Root>
            {#if !entry.content}<p class="live-reply-status">{entry.status==='streaming'?(entry.reasoning_content?'Reasoning is streaming. The answer will follow.':'Waiting for Glimmer…'):'No answer text was received. Any reasoning is preserved above.'}</p>{/if}
            {#if entry.status==='streaming'}<span class="stream-caret" aria-label="Generating"></span>{/if}
            <div class="live-turn-footer"><span>{entry.status==='stop'?'Completed':entry.status==='length'?'Output limit reached':entry.status==='stopped'?'Stopped · partial reply kept':entry.status==='error'?'Interrupted · not a completed reply':'Generating'}</span>{#if entry.content}<button class="icon-button" aria-label={entry.copied?'Response copied':'Copy response'} onclick={()=>copy(entry)}>{#if entry.copied}<Check size={17} aria-hidden="true"/>{:else}<Copy size={17} aria-hidden="true"/>{/if}</button>{/if}</div>
          </article>
        {/if}
      {/each}
    </div>
  </div>
</div>
<section class="composer-section" aria-label="Message composer">
  <div class="replay-toolbar"><span class="replay-meta">{view.busy?'Glimmer is generating':view.health?.ready?'Connected to Glimmer':'Kernel not connected'}</span><button class="text-button" onclick={refresh} disabled={view.busy} aria-label="Refresh kernel connection"><ArrowClockwise size={13} aria-hidden="true"/>Refresh</button></div>
  <PromptInput.Root class="muse-composer" onSubmit={submit} clearOnSubmit={false} maxFiles={0} maxFileSize={0} onError={()=>error='Attachments are not enabled.'}>
    <PromptInput.Body><label class="sr-only" for="live-message-input">Message Muse</label><PromptInput.Textarea id="live-message-input" bind:ref={textarea} bind:value={text} placeholder={view.health?.ready?'Message Muse…':'Waiting for the kernel connection…'} maxlength={4000} disabled={!view.health?.ready} aria-describedby="live-composer-helper"/></PromptInput.Body>
    <PromptInput.Toolbar class="composer-toolbar"><div class="composer-tools"><span><CircleNotch size={15} aria-hidden="true"/>Muse Glimmer</span><span class="composer-thinking">Reasoning on · temperature 0</span></div><PromptInput.Submit class="send-button" status={view.busy?'streaming':'ready'} onStop={()=>controller?.abort()} disabled={!view.busy&&(!view.health?.ready||!text.trim())} aria-label={view.busy?'Stop generating':'Send message'}>{#if view.busy}<Square size={15} weight="fill" aria-hidden="true"/>{:else}<ArrowUp size={20} weight="bold" aria-hidden="true"/>{/if}</PromptInput.Submit></PromptInput.Toolbar>
  </PromptInput.Root>
  {#if error}<p class="error-message" role="alert"><Info size={16} aria-hidden="true"/>{error}</p>{/if}
  {#if view.error}<p class="error-message connection-error" role="status"><Info size={16} aria-hidden="true"/>{view.error}</p>{/if}
  <p class="composer-helper" id="live-composer-helper">Real Glimmer responses. Enter sends; Shift + Enter adds a line. Chats stay in this browser session.</p>
</section>
