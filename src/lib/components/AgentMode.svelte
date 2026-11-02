<script lang="ts">
  import {Collapsible,Dialog,Switch} from 'bits-ui';
  import {agentEnabled,baseAgentContent} from '$lib/agent/model';
  import {emptyPi,lastRow} from '$lib/pi/model';
  import type {LiveState} from '$lib/live';
  import type {ProfileState,ProfileRevision} from 'glimmer-agent/profile-model';
  import type {SettingsMessage} from 'glimmer-agent/extension';
  import type {AgentBudgetStatus,AgentMode as BudgetMode} from '$lib/agent/budget';
  import AgentProfile from './AgentProfile.svelte';
  import ProfileDetails from './ProfileDetails.svelte';
  import WorkspaceFiles from './WorkspaceFiles.svelte';
  let {live,onPending,onSettings}:{live:LiveState;onPending:(pending:boolean)=>void;onSettings:(settings:SettingsMessage)=>void}=$props();
  let profile=$state<ProfileState|null>(null),loaded=$state(''),waiting=$state(false),error=$state(''),notice=$state('');
  let expanded=$state(false),optionsOpen=$state(false),editor=$state(false),choice=$state('current');
  let budget=$state<AgentBudgetStatus|null>(null);
  let observed='',serial=0;
  const enabled=$derived(loaded===live.conversation&&agentEnabled(profile?.active?.content));
  const custom=$derived(!enabled&&loaded===live.conversation&&(profile?.active?.content.tools?.length??0)>0);
  const ready=$derived(Boolean(live.pi?.connected&&live.conversation&&profile?.active&&loaded===live.conversation));
  const locked=$derived(!ready||waiting||live.busy||Boolean(profile?.pending));
  const revisions=$derived((live.pi?.entries??[]).filter(e=>e.kind==='glimmer.profile.changed').flatMap(e=>{
    const p=(e.data as {active?:ProfileRevision;pending?:ProfileRevision|null}).active;
    return p?[{entry:e.id,profile:p}]:[];
  }).filter((v,i,a)=>a.findIndex(x=>x.profile.revision===v.profile.revision)===i).reverse());
  const measured=$derived(lastRow(live.pi??emptyPi()));
  const workspace=$derived(measured?.record.workspace);
  const workspaceLayer=$derived(measured?.turn.header?.settings?.workspace?.layers[0]);
  const fileRevision=$derived((live.pi?.entries??[]).findLast(e=>e.kind==='pi.tool-result')?.id??0);
  async function load(id:string){
    const n=++serial;
    try{const [r,br]=await Promise.all([fetch(`/api/pi/profile?conversation=${encodeURIComponent(id)}`),fetch(`/api/pi/budget?conversation=${encodeURIComponent(id)}`)]),d=await r.json(),b=await br.json();
      if(n!==serial||live.conversation!==id)return;if(!r.ok)throw Error(d.error??'Agent profile unavailable.');
      if(!br.ok)throw Error(b.error??'Run budget unavailable.');
      profile=d.profile;budget=b;loaded=id;choice='current';
    }catch(e){if(n===serial&&live.conversation===id)error=String(e);}
  }
  $effect(()=>{
    const id=live.conversation,revision=live.pi?.entries.findLast(e=>e.kind==='glimmer.profile.changed'||e.kind.startsWith('glimmer.agent.'))?.id;
    const key=`${id}:${live.pi?.connected}:${revision}`;if(key===observed)return;observed=key;
    if(loaded!==id){profile=null;budget=null;loaded='';error='';notice='';expanded=false;}
    if(id&&live.pi?.connected)void load(id);
  });
  async function post(url:string,body:Record<string,unknown>){
    if(locked||!profile)return;
    const id=live.conversation!;waiting=true;onPending(true);error='';notice='';
    try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,conversation:id})}),d=await r.json();
      if(!r.ok)throw Error(d.error??'Agent configuration failed.');
      if(id!==live.conversation)return;
      if(d.profile){profile=d.profile;choice='current';}
      if(d.budget)budget=d.budget;
      if(d.settings)onSettings(d.settings);
      if(d.conversation){await live.openConversation?.(d.conversation);return;}
      notice=d.profile?.pending?'Saved pending; not active yet.':'Saved for this conversation.';
      if(body.enabled===true)expanded=true;
    }catch(e){if(id===live.conversation)error=e instanceof Error?e.message:String(e);}
    finally{waiting=false;onPending(false);}
  }
  function toggle(enabled:boolean){return post('/api/pi/agent',{version:profile?.version,enabled});}
  function setBudget(mode:BudgetMode){return post('/api/pi/budget',{version:budget?.settings.version,mode});}
  async function settleSavedRun(){
    // After a process crash Pi may be idle while the durable budget is still
    // running. Do not strand its explicit recovery behind the composer Stop.
    if(waiting||!ready||budget?.owned||budget?.run?.status!=='running')return;
    const id=live.conversation!;waiting=true;onPending(true);error='';
    try{const r=await fetch('/api/pi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'abort',conversation:id})}),d=await r.json();
      if(!r.ok)throw Error(d.error??'Saved run could not be stopped.');if(id===live.conversation){await load(id);notice='Saved run stopped. Inspect its files before starting again.';}
    }catch(e){if(id===live.conversation)error=e instanceof Error?e.message:String(e);}
    finally{waiting=false;onPending(false);}
  }
  function applyHarness(){
    if(!profile?.active||choice==='current')return;
    if(choice==='base')return post('/api/pi/profile',{action:'save',version:profile.version,content:baseAgentContent(profile.active.content)});
    return post('/api/pi/profile',{action:'restore',version:profile.version,entry:Number(choice)});
  }
</script>
<section class="agent-mode" class:expanded aria-label="Agent mode" data-testid="agent-mode">
  <div class="agent-heading">
    {#key `${live.conversation}:${profile?.version}:${waiting}`}<Switch.Root id="agent-mode-switch" class="agent-switch" checked={enabled} disabled={locked} onCheckedChange={toggle} aria-label="Agent mode" aria-describedby="agent-mode-help"><Switch.Thumb class="agent-thumb"/></Switch.Root>{/key}
    <label for="agent-mode-switch">Agent mode <span>{enabled?'· sandboxed · no network':custom?'· custom sandboxed tools':'· off'}</span></label>
    <Collapsible.Root bind:open={expanded} class="agent-expander">
      <Collapsible.Trigger class="agent-details-trigger">{expanded?'Hide agent workspace':'Harness & files'}</Collapsible.Trigger>
      <Collapsible.Content class="agent-content">
        <p class="agent-current">{profile?.active?.origin?`Imported harness · ${profile.active.origin.digest.slice(0,12)}`:'Glimmer Lab harness'} · r{profile?.active?.revision??'—'}<br/>{budget?.settings.mode==='agent'?'8,192 tokens/request · 32 requests · 64 tools · 20 minutes':'Chat budget · 6,144 tokens/reply'}</p>
        {#if budget?.run}<p class="agent-run-status" role="status">Last run · {budget.run.status==='running'?'running':budget.run.stopReason} · {budget.run.modelRequests} model requests · {budget.run.toolCalls} tool calls{budget.run.status==='running'&&!budget.owned?' · requires Stop after restart':''}</p>{/if}
        {#if budget?.run?.status==='running'&&!budget.owned}<button class="agent-recover" disabled={waiting||!ready} onclick={settleSavedRun}>Stop saved run</button>{/if}
        <Collapsible.Root bind:open={optionsOpen} class="agent-configuration"><Collapsible.Trigger class="agent-configure">Configure harness & budget</Collapsible.Trigger><Collapsible.Content>
        <div class="agent-budget">
          <label>Reply / run budget<select aria-label="Agent run budget" value={budget?.settings.mode??'chat'} disabled={locked} onchange={e=>setBudget(e.currentTarget.value as BudgetMode)}><option value="chat">Chat · 6,144 tokens per reply</option><option value="agent">Agent · 8,192 tokens per request</option></select></label>
          <p>{budget?.settings.mode==='agent'?'Agent run: 32 model requests · 64 tool calls · 20 minutes total. The clock and counters cover the whole user submission, including tool follow-ups.':'Chat budget: the existing 6,144-token reply cap. No additional Agent run limits.'} The serve can shorten a request to fit its remaining context; these limits do not guarantee completion.</p>
        </div>
        <div class="agent-harness">
          <label>Harness<select aria-label="Agent harness" bind:value={choice} disabled={locked}>
            <option value="current">Current{profile?.active?` · r${profile.active.revision}`:''}</option>
            <option value="base">Base · Glimmer Lab instructions</option>
            {#each revisions as rev}<option value={String(rev.entry)}>r{rev.profile.revision} · {rev.profile.origin?`Imported · ${rev.profile.origin.digest.slice(0,10)}`:'Saved profile'}</option>{/each}
          </select></label>
          <button onclick={applyHarness} disabled={locked||choice==='current'}>Apply harness</button>
          {#if choice!=='base'&&choice!=='current'}<button onclick={()=>post('/api/pi/profile',{action:'fork',entry:Number(choice)})} disabled={locked}>Fork with this harness</button>{/if}
          <Dialog.Root bind:open={editor}><Dialog.Trigger class="agent-editor-trigger" disabled={!ready}>Import / edit harness</Dialog.Trigger>
            <Dialog.Portal><Dialog.Overlay class="agent-editor-overlay"/><Dialog.Content class="agent-editor">
              <Dialog.Title>Conversation harness</Dialog.Title><Dialog.Description>Reuse a saved profile or import a text snapshot. No model-written extension runs on the host.</Dialog.Description>
              <AgentProfile {live}/><Dialog.Close class="agent-editor-close">Back to Agent mode</Dialog.Close>
            </Dialog.Content></Dialog.Portal>
          </Dialog.Root>
        </div>
        {#if profile?.active&&loaded===live.conversation}<div class="agent-provenance"><ProfileDetails profile={profile.active} label={`Inspect applied harness · r${profile.active.revision}`}/><span>{profile.active.origin?`Imported ${profile.active.origin.digest.slice(0,12)} · ${profile.active.origin.edited?'edited':'text snapshot'} · inline skills`:'Glimmer Lab profile · inline skills'} · {profile.active.content.tools?.length??0} tools</span></div>{/if}
        <p class="agent-disclosure">Applying a saved harness restores its tool selection too; check Agent mode afterwards. Forks have empty workspaces. Imported skills are inline, not the original Harness² execution environment. Shell is not enabled by Agent mode.</p>
        </Collapsible.Content></Collapsible.Root>
        <div class="agent-panels">
          <WorkspaceFiles conversation={live.conversation??''} refreshKey={`${live.conversation}:${fileRevision}`}/>
          <section class="agent-live-workspace" aria-label="Agent workspace readouts">
            <h3>Global workspace <span>MEASURED · TOP {workspace?.top[0]?.length??16}</span></h3>
            {#if workspace?.top[0]?.length}<p>L{workspaceLayer} · request {measured?.turn.request.slice(0,8)} · position {measured?.record.pos} · {measured?.record.channel??'channel unknown'}</p><ol>{#each workspace.top[0] as [id,score]}<li><span>{live.labels?.[String(id)]??`#${id}`}</span><small>{score.toFixed(2)}</small></li>{/each}</ol>{:else}<p>No workspace rows measured yet. Agent mode enables L44 top-16 plus emotion readouts; nothing is substituted.</p>{/if}
            {#if measured&&measured.turn.request!==live.pi?.turns.at(-1)?.request}<p class="prior-reading">Last measured request; waiting for the next tool-followup readout.</p>{/if}
            <p>Emotion directions stay in the dock below. These are model readouts, not a tool result or a correctness grade.</p>
          </section>
        </div>
      </Collapsible.Content>
    </Collapsible.Root>
  </div>
  <p id="agent-mode-help">{waiting?'Checking isolation and Mojo toolchain…':enabled?'Read, write, edit, list, find, grep, and Mojo build/run are enabled for this conversation.':'Off by default. Turning on checks isolation and the Mojo toolchain, then enables sandboxed tools and live readouts.'}</p>
  {#if profile?.pending}<p>Profile change pending. Wait for the current run to settle before changing Agent mode.</p>{/if}
  {#if loaded===live.conversation&&profile?.active?.content.tools?.includes('bash')}<p>Sandboxed shell is also selected in this saved profile. It was not enabled by the Agent mode switch.</p>{/if}
  {#if error}<p class="agent-error" role="alert">{error}</p>{/if}{#if notice}<p role="status">{notice}</p>{/if}
</section>
<style>
  .agent-mode{margin:0 24px;padding:11px 0;border-bottom:1px solid var(--color-rule);color:var(--color-ink);flex:none}.agent-heading{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.agent-heading>label{font-size:13px;font-weight:500}.agent-heading>label span{font-size:11px;font-weight:400;color:var(--color-muted)}.agent-mode p{font-size:11px;line-height:1.55;color:var(--color-muted);margin:7px 0}.agent-mode .agent-error{color:var(--color-danger,#ad3e3e)}
  .agent-mode :global(.agent-switch){display:flex;align-items:center;flex:none;width:34px;height:20px;padding:2px;border:1px solid var(--color-faint);border-radius:12px;background:var(--color-rule)}.agent-mode :global(.agent-switch[data-state=checked]){background:var(--color-accent)}.agent-mode :global(.agent-switch:disabled){opacity:.5}.agent-mode :global(.agent-thumb){display:block;width:14px;height:14px;border-radius:50%;background:var(--color-paper);transform:translateX(0)}.agent-mode :global(.agent-thumb[data-state=checked]){transform:translateX(13px)}
  .agent-mode :global(.agent-content){width:100%;flex-basis:100%;max-height:42vh;overflow:auto}.agent-mode :global(.agent-expander){display:contents}.agent-mode :global(.agent-details-trigger){margin-left:auto;font-size:11px;color:var(--color-accent);padding:5px 8px;border:1px solid var(--color-rule);border-radius:5px;background:transparent}
  .agent-harness{display:flex;align-items:end;gap:8px;flex-wrap:wrap;margin-top:10px}.agent-harness label{display:grid;gap:5px;font-size:11px}.agent-harness select{max-width:290px;background:var(--color-paper-2);color:var(--color-ink);padding:7px;border:1px solid var(--color-rule);border-radius:5px}.agent-harness button,.agent-harness :global(.agent-editor-trigger){font-size:11px;padding:7px 9px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink)}button:disabled{opacity:.5}.agent-provenance{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:9px 0;font:10px/1.6 var(--font-mono);color:var(--color-muted)}
  .agent-budget{padding:8px 0 12px;border-bottom:1px solid var(--color-rule)}.agent-budget label{display:grid;gap:6px;font-size:11px}.agent-budget select{max-width:100%;padding:7px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper);color:var(--color-ink)}.agent-run-status{font-family:var(--font-mono)}
  .agent-current{font-family:var(--font-mono)}.agent-mode :global(.agent-configure){width:100%;text-align:left;padding:9px 0;color:var(--color-accent);border:0;border-block:1px solid var(--color-rule);background:transparent;font-size:11px}
  .agent-recover{font-size:11px;padding:7px 9px;border:1px solid var(--color-rule);border-radius:5px;background:var(--color-paper-2);color:var(--color-ink)}
  .agent-panels{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:12px 0}.agent-live-workspace{min-width:0}.agent-live-workspace h3{font-size:12px;font-weight:500}.agent-live-workspace h3 span{font:9px var(--font-mono);color:var(--color-muted);margin-left:8px}.agent-live-workspace ol{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));list-style:none;margin:8px 0;padding:0;gap:2px 10px}.agent-live-workspace li{display:flex;gap:8px;justify-content:space-between;border-bottom:1px solid var(--color-rule);font:11px/1.7 var(--font-mono)}.agent-live-workspace li span{overflow:hidden;text-overflow:ellipsis;white-space:pre}.agent-live-workspace small{color:var(--color-muted)}
  :global(.agent-editor-overlay){position:fixed;inset:0;background:#0008;z-index:80}:global(.agent-editor){position:fixed;z-index:81;left:50%;top:50%;transform:translate(-50%,-50%);width:min(850px,calc(100vw - 28px));max-height:90vh;overflow:auto;padding:24px;border:1px solid var(--color-rule);border-radius:10px;background:var(--color-paper);color:var(--color-ink)}:global(.agent-editor>h2){font-size:19px}:global(.agent-editor>p){font-size:12px;line-height:1.7;color:var(--color-muted)}:global(.agent-editor-close){margin-top:16px;border:1px solid var(--color-rule);border-radius:5px;padding:8px 12px;background:var(--color-paper-2);color:var(--color-ink)}
  @media(max-width:700px){.agent-mode{margin:0 14px}.agent-panels{grid-template-columns:1fr}.agent-mode :global(.agent-content){max-height:36vh}.agent-harness select{max-width:100%}}
  @media(min-width:1100px){
    :global(.video-shell .lab-chat){position:relative}
    :global(.video-shell .lab-chat:has(>.agent-mode.expanded)>.conversation-stage){margin-right:380px}
    :global(.video-shell .lab-chat:has(>.agent-mode.expanded)>.composer-section),:global(.video-shell .lab-chat:has(>.agent-mode.expanded)>.lab-live-strip){width:calc(100% - 380px);margin-left:0;margin-right:380px}
    :global(.video-shell .lab-chat)>.agent-mode.expanded{margin-right:404px}
    :global(.video-shell .lab-chat)>.agent-mode :global(.agent-content){position:absolute;inset:0 0 0 auto;width:380px;max-height:none;padding:16px;overflow:auto;border-left:1px solid var(--color-rule);background:var(--color-paper-2)}
    :global(.video-shell .lab-chat) .agent-panels{grid-template-columns:minmax(0,1fr)}
    :global(.video-shell .lab-chat) .agent-live-workspace{order:-1}
  }
</style>
