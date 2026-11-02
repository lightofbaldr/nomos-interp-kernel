<script lang="ts">
  import {onMount} from 'svelte';
  import {Collapsible} from 'bits-ui';
  import * as Message from '@nomos/svelte-ai-elements/message';
  import * as PromptInput from '@nomos/svelte-ai-elements/prompt-input';
  import * as Reasoning from '@nomos/svelte-ai-elements/reasoning';
  import {CircleNotch,ArrowUp,Square,Brain,CaretDown,Info,ArrowClockwise} from 'phosphor-svelte';
  import {emptyLiveState,type LiveState} from '$lib/live';
  import {emptyPi,reducePi,messagesOf,textOf,type Assets} from '$lib/pi/model';
  import {emotionDirections} from '$lib/pi/assets';
  import {readBridge,MaintenanceDisconnect} from '$lib/pi/stream';
  import {defaultRules,evaluateAlerts,validRules,type AlertRules,type AlertResult} from '$lib/pi/alerts';
  import AlertTrail from './AlertTrail.svelte';
  import MetaAnnotation from './MetaAnnotation.svelte';
  import {annotationFor} from '$lib/pi/annotation';
  import ProfileDetails from './ProfileDetails.svelte';
  import FileToolCalls from './FileToolCalls.svelte';
  import AgentMode from './AgentMode.svelte';
  import type {ProfileUsed} from 'glimmer-agent/profile-model';
  import {cappedReasoningOnly,generationSettings,generationFor,generationLabel,effectiveReplyBudget,retryTarget,wasRetried,type GenerationUsed} from '$lib/pi/generation';

  let {onState,compact=false,startNew=false,requestedConversation=''}:{onState:(state:LiveState)=>void;compact?:boolean;startNew?:boolean;requestedConversation?:string}=$props();
  let view=$state.raw<LiveState>(emptyLiveState()),pi=$state.raw(emptyPi()),text=$state(''),error=$state('');
  let sessions=$state<{id:string;busy:boolean;title?:string}[]>([]),conversation=$state(''),opening=$state(false);
  let metaAvailable=$state(false);
  let autoCreate=false;
  let controlPending=$state(false);
  let requestedSeen='';
  let scroll:HTMLDivElement,textarea:HTMLTextAreaElement|null=$state(null);
  let alive=false,revision=0,controller:AbortController|undefined,follow=true,frame=0,labelTimer:ReturnType<typeof setTimeout>|undefined;
  let labelFlight=false,labelFailed=false;const labels:Record<string,string|null>={};
  let rules=defaultRules(),labelVersion=0,alertKey='',alertRows:unknown[]=[],alerts:AlertResult|undefined;
  const messages=$derived(messagesOf(pi));
  const decoding=$derived(generationSettings(pi.entries));
  const agentBudget=$derived((pi.entries.findLast(e=>e.kind==='glimmer.agent.changed')?.data as {mode?:string}|undefined)?.mode==='agent');
  const retryId=$derived(retryTarget(pi.entries));
  const partialDecoding=$derived(pi.entries.findLast(e=>e.kind==='glimmer.generation.used')?.data as GenerationUsed|undefined);
  function setAlertRules(next:AlertRules){validRules(next);rules=structuredClone(next);update();}
  function update(next:Partial<LiveState>={}){if(!alive)return;
    const key=`${conversation}:${labelVersion}:${JSON.stringify(rules)}`,rows=pi.turns.map(t=>t.records);
    if(key!==alertKey||rows.length!==alertRows.length||rows.some((r,i)=>r!==alertRows[i])){alerts=evaluateAlerts(pi,rules,labels);alertKey=key;alertRows=rows;}
    // A durable input can arrive after submit's acknowledgement. Derive its title
    // from that entry; don't race a list fetch or poll the kernel during a turn.
    const firstUser=pi.entries.flatMap(e=>e.model??[]).find(m=>m.role==='user');
    const title=firstUser?textOf(firstUser).trim().replace(/\s+/g,' ').slice(0,90):'';
    const conversations=sessions.map(c=>c.id===conversation?{...c,...(title?{title}:{}),...(pi.connected?{busy:pi.busy}:{})}:c);
    view={...view,...next,pi,busy:pi.busy,conversation,labels:{...labels},command,controlPending,setSampling:sampling,reconnect,alertRules:rules,alerts,setAlertRules,conversations,metaAvailable,newConversation:create,openConversation:async id=>{await refresh();attach(id);},
      select:(selection)=>update({selection}),selectLayer:(emotionLayer)=>update({emotionLayer})};onState(view);}
  function setControlPending(value:boolean){controlPending=value;update();}
  function latest(){if(!follow)return;cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{if(scroll)scroll.scrollTop=scroll.scrollHeight;});}
  async function api(body:Record<string,unknown>){
    const r=await fetch('/api/pi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw Error(data.error??'Agent operation failed.');return data;
  }
  async function refresh(){
    const r=await fetch('/api/pi'),data=await r.json();if(!r.ok)throw Error(data.error??'Agent unavailable.');
    if(!alive)return;sessions=data.conversations;autoCreate=data.autoCreate===true;metaAvailable=data.annotator?.available===true;update({health:data.health,assets:data.assets,error:data.error??''});
  }
  async function refreshHistory(){
    const r=await fetch('/api/pi?history=1'),data=await r.json();if(!r.ok)throw Error(data.error??'History unavailable.');
    if(!alive)return;
    sessions=data.conversations;
    if(conversation&&!sessions.some(s=>s.id===conversation)){
      revision++;controller?.abort();conversation='';pi=emptyPi();text='';error='';
      localStorage.removeItem('glimmer.pi.conversation');
      if(!sessions.length)localStorage.setItem('glimmer.pi.empty','true');
      update({selection:undefined});
      window.dispatchEvent(new Event('glimmer-data-purged'));
    }else update();
  }
  async function command(action:'readout'|'steer',value:unknown){
    const id=conversation;const data=await api({action,conversation:id,[action]:value});
    if(id===conversation&&alive){pi=reducePi(pi,data);update();}
  }
  function scheduleLabels(){
    if(labelTimer||labelFlight||labelFailed||!view.assets?.tokenizer)return;
    labelTimer=setTimeout(()=>{labelTimer=undefined;void fetchLabels();},250);
  }
  async function fetchLabels(){
    if(labelFlight)return;const ids=new Set<number>();
    for(const t of pi.turns)for(const r of t.records){if(!(r.token in labels))ids.add(r.token);for(const top of r.workspace?.top??[])for(const [id] of top)if(!(id in labels))ids.add(id);}
    if(!ids.size)return;labelFlight=true;
    try{const data=await api({action:'labels',ids:[...ids].slice(0,2048)});
      if(data.sha256!==view.assets?.tokenizer)throw Error('Token-label identity changed.');
      Object.assign(labels,data.labels);labelVersion++;update();
    }catch{labelFailed=true;update({error:'Token labels unavailable. Raw token IDs are shown; unknown is not absence.'});}
    finally{labelFlight=false;if(alive)scheduleLabels();}
  }
  function attach(id:string){
    const rev=++revision;controller?.abort();controller=new AbortController();const signal=controller.signal;
    conversation=id;pi=emptyPi();update({selection:undefined});localStorage.setItem('glimmer.pi.conversation',id);
    void (async()=>{
      try{const response=await fetch(`/api/pi?conversation=${encodeURIComponent(id)}`,{signal});
        await readBridge(response,wire=>{if(!alive||rev!==revision)return;pi=reducePi(pi,wire);update();latest();scheduleLabels();});
        if(!signal.aborted)throw Error('Bridge disconnected. Reconnect for a fresh snapshot; generation may still be running.');
      }catch(e){if(alive&&rev===revision&&!signal.aborted){pi={...pi,connected:false};error=e instanceof Error?e.message:'Bridge failed.';update();
        // A purge closes the old harness, including surviving sessions' bridges.
        // Retry only this read-only history check; never retry a generation.
        for(let n=0;e instanceof MaintenanceDisconnect&&n<120&&alive&&rev===revision&&!signal.aborted;n++){
          try{await refreshHistory();if(conversation&&rev===revision){error='';attach(conversation);}break;}
          catch{await new Promise(resolve=>setTimeout(resolve,500));}
        }
      }}
    })();
  }
  async function create(){
    if(opening)return;opening=true;error='';
    try{const data=await api({action:'create'});if(!alive)return;
      const id=data.conversation as string,a=view.assets as Assets|undefined;
      const d=emotionDirections(a);
      if(a?.supported&&d){
        const layers=[20,24,29].filter(l=>d.layers.includes(l));
        if(layers.length)await api({action:'readout',conversation:id,readout:{enabled:true,emotion:{directions:d.id,layers},workspace:null}});
      }
      localStorage.removeItem('glimmer.pi.empty');await refresh();attach(id);text='';follow=true;
    }catch(e){error=e instanceof Error?e.message:'Could not create conversation.';}finally{opening=false;}
  }
  export function reset(){void create();}
  async function submit(message:{text:string}){
    if(!pi.connected||pi.busy||controlPending||!message.text.trim())return;
    const id=conversation;setControlPending(true);
    error='';try{await api({action:'submit',conversation:id,text:message.text});if(conversation===id)text='';}catch(e){if(conversation===id)error=e instanceof Error?e.message:'Submit failed.';}finally{setControlPending(false);}
  }
  async function sampling(value:boolean){
    if(!decoding||controlPending||pi.busy||!pi.connected)return;
    const id=conversation;setControlPending(true);error='';
    try{await api({action:'generation',conversation:id,version:decoding.version,sampling:value});}
    catch(e){if(conversation===id)error=e instanceof Error?e.message:'Could not save decoding.';throw e;}
    finally{setControlPending(false);}
  }
  async function retry(target:number){
    if(controlPending||pi.busy||!pi.connected)return;
    const id=conversation;setControlPending(true);error='';
    try{await api({action:'retry',conversation:id,target});}
    catch(e){if(conversation===id)error=e instanceof Error?e.message:'Retry failed.';}
    finally{setControlPending(false);}
  }
  async function stop(){try{await api({action:'abort',conversation});}catch(e){error=String(e);}}
  async function reconnect(){error='';try{await refresh();if(conversation)attach(conversation);}catch(e){error=String(e);}}
  onMount(()=>{
    alive=true;void (async()=>{try{await refresh();if(!alive)return;
      const saved=requestedConversation||localStorage.getItem('glimmer.pi.conversation');
      requestedSeen=requestedConversation;
      if(startNew&&view.health?.ready)await create();
      else if(saved&&sessions.some(s=>s.id===saved))attach(saved);else if(sessions[0])attach(sessions[0].id);else if(view.health?.ready&&autoCreate&&!localStorage.getItem('glimmer.pi.empty'))await create();
    }catch(e){error=e instanceof Error?e.message:'Connection failed.';}})();
    const purged=()=>{void refreshHistory().catch(()=>{});};
    const storage=(e:StorageEvent)=>{if(e.key==='glimmer.pi.purge')purged();};
    window.addEventListener('glimmer-data-purged',purged);window.addEventListener('storage',storage);
    return()=>{alive=false;revision++;controller?.abort();clearTimeout(labelTimer);cancelAnimationFrame(frame);window.removeEventListener('glimmer-data-purged',purged);window.removeEventListener('storage',storage);};
  });
  $effect(()=>{if(!requestedConversation){requestedSeen='';return;}if(alive&&requestedConversation!==requestedSeen&&sessions.some(s=>s.id===requestedConversation)){requestedSeen=requestedConversation;if(requestedConversation!==conversation)attach(requestedConversation);}});
</script>

{#if !compact}<div class="pi-session-bar">
  <label>Conversation <select aria-label="Pi conversation" value={conversation} onchange={e=>attach(e.currentTarget.value)} disabled={opening}>{#each sessions as s}<option value={s.id}>Session {s.id}{s.busy?' · generating':''}</option>{/each}</select></label>
  <button class="text-button" onclick={create} disabled={opening}>New session</button><span>{pi.connected?'Pi connected':'Disconnected'}</span>
</div>{/if}
<AgentMode live={view} onPending={pending=>{controlPending=pending;update();}} onSettings={settings=>{pi=reducePi(pi,settings);update();}}/>
<div class="conversation-stage">
  <img class="chat-mascot" src="/img/muse-mascot.webp" alt="" aria-hidden="true" width="1129" height="1393" decoding="async"/>
  <div class="conversation-scroll" id="conversation" tabindex="-1" bind:this={scroll} onscroll={()=>follow=scroll.scrollHeight-scroll.clientHeight-scroll.scrollTop<80}>
    <div class="conversation-body live-conversation" aria-label="Live conversation">
      {#if !messages.length}<div class="welcome live-welcome"><span class="welcome-mark"><CircleNotch aria-hidden="true" size={43} weight="bold"/></span><h1>Space to think.<br/>A way to look closer.</h1><p>{view.health?.ready?'Glimmer is ready. Start a conversation and follow its live readouts.':'Waiting for the configured Glimmer service.'}</p>{#if !conversation&&view.health?.ready}<button class="lab-button empty-start" onclick={create} disabled={opening}>Start a conversation</button>{/if}</div>{/if}
      {#each messages as entry (entry.id)}
        {@const answer=textOf(entry.message)}{@const thinking=textOf(entry.message,'thinking')}
        {@const sourceEntry=pi.entries.find(e=>String(e.id)===entry.id.split(':')[0])}
        {@const measuredTurn=sourceEntry?.byTaskId?pi.turns.find(t=>t.task===sourceEntry.byTaskId):undefined}
        {@const used=sourceEntry?.byTaskId?pi.entries.filter(e=>e.kind==='glimmer.profile.used').map(e=>e.data as ProfileUsed).findLast(p=>p.task===sourceEntry.byTaskId):undefined}
        {@const decodingUsed=entry.streaming?partialDecoding:generationFor(pi.entries,sourceEntry?.byTaskId,undefined,sourceEntry?.id)}
        {@const retried=sourceEntry?wasRetried(pi.entries,sourceEntry.id):false}
        {#if entry.message.role==='user'}<Message.Root from="user" class="user-message live-user"><Message.Content class="message-copy"><div class="live-plain">{answer}</div></Message.Content></Message.Root>
        {:else}<article class="assistant-message live-assistant" aria-label="Live assistant response">
          <div class="assistant-identity"><span class="muse-mark"><CircleNotch aria-hidden="true" size={25}/></span><strong>Muse</strong><span class="assistant-source">Live · Pi / Nomos</span></div>
          {#if thinking}<Reasoning.Root class="live-reasoning" defaultOpen={false} isStreaming={entry.streaming&&!answer}>
            <Reasoning.Trigger class="live-reasoning-trigger"><Brain aria-hidden="true" size={17}/><span>Show reasoning</span><CaretDown aria-hidden="true" size={14}/></Reasoning.Trigger>
            <Collapsible.Content class="live-reasoning-content"><div class="live-plain reasoning-text">{thinking}</div></Collapsible.Content>
          </Reasoning.Root>{/if}
          <Message.Root from="assistant" class="assistant-body"><Message.Content class="message-copy"><div class="response-text live-plain" aria-busy={entry.streaming}>{answer}</div></Message.Content></Message.Root>
          <FileToolCalls message={entry.message} entries={pi.entries} streaming={entry.streaming}/>
          {#if !answer&&!(entry.message.role==='assistant'&&entry.message.content.some(b=>b.type==='toolCall'))}<p class="live-reply-status">{entry.streaming?'Waiting for answer text…':retried?'Stopped at the reply limit, retried.':cappedReasoningOnly(entry.message)?'Stopped at the reply limit; no answer text. Reasoning is preserved above.':'No answer text received. Reasoning is preserved above.'}</p>{/if}
          <div class="live-turn-footer">{entry.streaming?'Generating':entry.message.role==='assistant'?`Generation: ${entry.message.stopReason}`:''}</div>
          <div class="decoding-attribution" data-testid="turn-decoding">{generationLabel(decodingUsed)}</div>
          {#if decodingUsed?.budget}{@const ceiling=effectiveReplyBudget(decodingUsed,measuredTurn)}
            {#if ceiling?.clamped}<p class="error-message" data-testid="context-cap-warning">Context-limited: derived ceiling {ceiling.effective.toLocaleString()} tokens, below the requested {ceiling.requested.toLocaleString()}. Based on this request’s {ceiling.prompt.toLocaleString()}-token prompt and advertised {ceiling.capacity.toLocaleString()} context; the serve does not return its effective limit.</p>
            {:else if !ceiling}<div class="decoding-attribution">Effective reply limit unknown; the serve may shorten the requested limit to fit context.</div>{/if}
          {/if}
          {#if !entry.streaming&&sourceEntry&&sourceEntry.id===retryId}<div class="retry-panel"><button class="text-button" onclick={()=>retry(sourceEntry.id)} disabled={controlPending||pi.busy||!pi.connected||!view.health?.ready}><ArrowClockwise size={14} aria-hidden="true"/>Retry</button><p>Reuses the same question with the saved controls. Greedy may repeat the same result; sampling is optional in Controls.</p></div>{/if}
          {#if measuredTurn&&!entry.streaming}<p class="readout-attribution">Readout request {measuredTurn.request.slice(0,8)} · {measuredTurn.status} · {measuredTurn.records.length.toLocaleString()} measured positions</p>{#if compact}<a class="inspect-link" href={`/workspace?conversation=${conversation}&turn=${encodeURIComponent(measuredTurn.request)}`}>Inspect this turn’s workspace →</a>{/if}{/if}
          <details class:turn-details={compact} open={!compact}><summary class:sr-only={!compact}>Turn details &amp; second opinion</summary>
          {#if !entry.streaming}{#if used}<ProfileDetails profile={used.profile} label={`Profile r${used.profile.revision} · inline skills · ${used.profile.content.tools?.length??0} tools`}/>{:else}<small>Profile unrecorded for this historical turn.</small>{/if}{/if}
          {#if measuredTurn?.status==='complete'&&!entry.streaming}<MetaAnnotation {conversation} request={measuredTurn.request} turn={measuredTurn.turn} rules={view.alertRules??defaultRules()} available={metaAvailable} saved={annotationFor(pi.entries,measuredTurn.request)}/>{/if}
          </details>
        </article>{/if}
      {/each}
      {#if !compact}<AlertTrail live={view}/>{/if}
    </div>
  </div>
</div>
<section class="composer-section" aria-label="Message composer">
  <PromptInput.Root class="muse-composer" onSubmit={submit} clearOnSubmit={false} maxFiles={0} maxFileSize={0}>
    <PromptInput.Body><label class="sr-only" for="live-message-input">Message Muse</label><PromptInput.Textarea id="live-message-input" bind:ref={textarea} bind:value={text} placeholder="Message Muse…" maxlength={4000} disabled={!pi.connected||!view.health?.ready} aria-describedby="live-composer-helper"/></PromptInput.Body>
    <PromptInput.Toolbar class="composer-toolbar"><div class="composer-tools"><span>Muse Glimmer</span><span class="composer-thinking">Pi · reasoning on · {agentBudget?'8,192-token request · 20-minute run':'6,144-token reply limit'}</span></div><PromptInput.Submit class="send-button" status={pi.busy?'streaming':'ready'} onStop={stop} disabled={!pi.busy&&(controlPending||!pi.connected||!view.health?.ready||!text.trim())} aria-label={pi.busy?'Stop generating':'Send message'}>{#if pi.busy}<Square size={15} weight="fill" aria-hidden="true"/>{:else}<ArrowUp size={20} weight="bold" aria-hidden="true"/>{/if}</PromptInput.Submit></PromptInput.Toolbar>
  </PromptInput.Root>
  {#each [...new Set([error,view.error,...pi.errors])].filter(Boolean) as message}<p class="error-message" role="alert"><Info size={16} aria-hidden="true"/>{message}</p>{/each}
  <p class="composer-helper" id="live-composer-helper">Saved on this server. Switching chats or closing the page does not stop generation. Stop is explicit.</p>
</section>
<style>
  .empty-start{margin-top:20px}
  .readout-attribution{font:10px/1.6 var(--font-mono);color:var(--color-muted);margin:6px 0}
  .pi-session-bar{display:flex;align-items:center;gap:12px;padding:8px 24px;border-bottom:1px solid var(--color-rule);font-size:11px;color:var(--color-muted);flex-wrap:wrap}
  .pi-session-bar label{display:flex;align-items:center;gap:8px}.pi-session-bar select{max-width:180px;background:var(--color-paper-2);color:var(--color-ink);border:1px solid var(--color-rule);padding:7px;border-radius:6px}.pi-session-bar>span{margin-left:auto}
  .decoding-attribution{font:10px/1.7 var(--font-mono);color:var(--color-muted);margin:4px 0 9px}.retry-panel{display:flex;gap:14px;align-items:center;margin:10px 0}.retry-panel button{display:flex;gap:6px;align-items:center;flex:none;min-height:36px}.retry-panel p{font-size:11px;line-height:1.6;color:var(--color-muted);max-width:65ch}
</style>
