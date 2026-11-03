import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {createModels} from '@earendil-works/pi-ai';
import {BACKGROUND_CONTEXT as ctx,createRegistry,Harness,LiveDoc,InboxDoc,openNodeSqliteStorage,type ConversationId,type EntryId,type Storage} from 'glimmer-agent/pi';
import {ProfileHost,profileExtension} from 'glimmer-agent/profile';
import type {ProfileOrigin,SandboxTool} from 'glimmer-agent/profile-model';
import {fileToolsExtension,type FileBroker} from 'glimmer-agent/file-tools';
import type {ToolAvailability} from './file-broker.ts';
import {ReadoutHost,readoutExtension,type ReadoutConfig,type Steer} from 'glimmer-agent/extension';
import {NOMOS_MODEL,NOMOS_PROVIDER,nomosProvider} from 'glimmer-agent/provider';
import {openBridge,type Bridge,type BridgeMessage} from 'glimmer-agent/bridge';
import type {Assets} from '../pi/model.ts';
import {GenerationHost} from './generation.ts';
import {REPLY_CAP,CHAT_TIMEOUT_MS} from '../pi/generation.ts';
import {unusedDatabase} from './conversation-data.ts';
import {agentContent,agentReadout} from '../agent/model.ts';
import {AgentBudgetHost} from './agent-budget.ts';
import type {AgentMode,AgentBudgetReason} from '../agent/budget.ts';

export function checkedBase(value:string){
  const url=new URL(value);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||!['','/'].includes(url.pathname))throw Error('Invalid operator-configured kernel URL.');
  return url.origin;
}
export async function createRuntime(baseUrl:string, database:string|Storage,files?:FileBroker & {status():ToolAvailability;close():Promise<void>}){
  const budget=new AgentBudgetHost(ctx,{abort:async(id)=>{
    const chat=await harness.conversation(id as ConversationId,ctx);
    if(chat){await chat.abort(ctx);await host.settled(chat.id);}
  }});
  const fileExtension=files?fileToolsExtension({ready:tools=>files.ready(tools),execute:(id,tool,args,signal)=>budget.executeTool(id,tool,s=>files.execute(id,tool,args,s),signal)}):null;
  const base=checkedBase(baseUrl), host=new ReadoutHost(ctx), profiles=new ProfileHost(ctx,files?{ready:tools=>files.ready(tools),tools:fileExtension!.tools!}:undefined), models=createModels(), registry=createRegistry();
  const generation=new GenerationHost(ctx,budget);
  models.setProvider(generation.provider(nomosProvider({baseUrl:base+'/v1',requests:host.requests,maxTokens:REPLY_CAP})));
  registry.install(budget.extension());
  registry.install(readoutExtension(host));
  registry.install(profileExtension(profiles));
  registry.install(generation.extension());
  if(fileExtension)registry.install(fileExtension);
  if(typeof database==='string')await mkdir(path.dirname(database),{recursive:true,mode:0o700});
  const storage=typeof database==='string'?await openNodeSqliteStorage(database):database;
  const harness=await Harness.open(storage,
    {models,registry,settings:{retry:{enabled:false},compaction:{enabled:false},stream:{maxRetries:0,timeoutMs:CHAT_TIMEOUT_MS}}},ctx);
  host.attach(harness);
  profiles.attach(harness);
  generation.attach(harness);
  budget.attach(harness);
  let active:number|null=null,closing=false,configuring=false;
  const settlements=new Set<Promise<void>>();
  function trackSettlement(work:Promise<void>,id:ConversationId){
    const tracked=work.catch(e=>host.report(id,null,e)).finally(()=>{settlements.delete(tracked);active=null;});
    settlements.add(tracked);void tracked.catch(()=>{});
  }
  function configurationFree(){if(configuring)throw Error('Agent configuration is being checked. Wait for it to finish.');}
  const bridges=new Set<Bridge>();
  async function conversation(id:string){
    if(!/^[1-9]\d{0,14}$/.test(id)||!Number.isSafeInteger(Number(id)))throw Error('Invalid conversation ID.');
    const chat=await harness.conversation(Number(id) as ConversationId,ctx);
    if(!chat)throw Error('Conversation not found.');return chat;
  }
  async function finish(chat:Awaited<ReturnType<typeof conversation>>,runId:string,reason:AgentBudgetReason){
    await chat.waitForIdle(ctx);await host.settled(chat.id);await budget.finish(chat,runId,reason);
  }
  const api={host,harness,
    busy(){return active!==null||configuring;},
    async assertConversation(id:string){await conversation(id);},
    async canAutoCreate(){return typeof database==='string'?unusedDatabase(path.resolve(database)):false;},
    async assertIdle(){
      if(closing||active!==null||configuring)throw Error('A conversation is still generating or configuring. Wait for it to finish or stop it before deleting data.');
      const work=await harness.inspect(ctx);
      if(work.tasks.length||work.submissions.length)throw Error('Saved conversation work is still pending. Wait for it to settle before deleting data.');
      let cursor;
      do{
        const batch=await storage.scanConversations({order:'ascending'},100,cursor,ctx);cursor=batch.next;
        for(const c of batch.items){
          const live=await harness.snapshot(LiveDoc,c.id,ctx),inbox=await harness.snapshot(InboxDoc,c.id,ctx);
          if(live?.run||live?.generation||live?.tools?.length||live?.nestedTools?.length||live?.compactions?.length||inbox?.items.length)throw Error('Saved conversation state is not settled. Stop or resolve it before deleting data.');
        }
      }while(cursor);
    },
    toolStatus(){return files?.status()??{available:false,reason:'No sandbox broker configured.',policy:'glimmer.files/bwrap-1'};},
    async checkTools(tools:readonly SandboxTool[]=[]){if(!files)throw Error('No sandbox broker configured.');await files.ready(tools);return files.status();},
    async setAgentMode(id:string,version:number,enabled:boolean,assets?:Assets){
      if(typeof enabled!=='boolean'||!Number.isSafeInteger(version)||version<1)throw Error('Invalid Agent mode setting.');
      if(closing||active!==null||configuring)throw Error('Wait for the conversation to settle before changing Agent mode.');
      // Claim synchronously before probes/validation. No generation or second
      // profile writer may slip between the checks and the profile commit.
      configuring=true;
      try{
        const work=await harness.inspect(ctx);
        if(work.tasks.length||work.submissions.length)throw Error('Saved conversation work is still pending.');
        const chat=await conversation(id),state=await profiles.get(chat);
        const live=await harness.snapshot(LiveDoc,chat.id,ctx),inbox=await harness.snapshot(InboxDoc,chat.id,ctx);
        if(live?.run||live?.generation||live?.tools?.length||inbox?.items.length||state.pending)throw Error('Wait for the saved conversation and pending profile to settle.');
        if(state.version!==version||!state.active)throw Error('The profile changed. Reload before changing Agent mode.');
        const content=agentContent(state.active.content,enabled);
        const readout=enabled?agentReadout(assets!, (await host.settings(chat.id)).readout):null;
        if(readout)validateReadout(readout,assets!);
        if(enabled){if(!files)throw Error('No sandbox broker configured.');await files.ready(content.tools);}
        // Checks fail without changing anything. Readouts are saved first so
        // tools are never enabled by this action with missing capture settings.
        // A storage failure can leave readouts on, but does not claim success.
        if(readout)await host.setReadout(chat,readout);
        const savedBudget=await budget.settings(chat);
        await budget.save(chat,savedBudget.version,enabled?'agent':'chat');
        const profile=await profiles.save(chat,version,content);
        return {profile,tools:api.toolStatus(),settings:await host.settings(chat.id),budget:await budget.status(chat)};
      }finally{configuring=false;}
    },
    async list(){
      const items=(await storage.scanConversations({order:'descending'},100,undefined,ctx)).items;
      return Promise.all(items.map(async c=>{
        let batch=await storage.scanEntries({conversationId:c.id,order:'ascending'},16,undefined,ctx);
        let first=batch.items.find(e=>e.model?.some(m=>m.role==='user'));
        while(!first&&batch.next){batch=await storage.scanEntries({conversationId:c.id,order:'ascending'},16,batch.next,ctx);first=batch.items.find(e=>e.model?.some(m=>m.role==='user'));}
        const m=first?.model?.find(m=>m.role==='user');
        const text=typeof m?.content==='string'?m.content:m?.content.filter(b=>b.type==='text').map(b=>b.text).join('')??'';
        return {id:String(c.id),busy:active===c.id,title:text.trim().replace(/\s+/g,' ').slice(0,90)||'New conversation'};
      }));
    },
    async create(){
      if(closing)throw Error('Agent is closing.');
      if((await api.list()).length>=100)throw Error('Local conversation limit reached.');
      const chat=await harness.createConversation({ownership:{kind:'ownerless'},agent:{model:{provider:NOMOS_PROVIDER,modelId:NOMOS_MODEL},tools:[],
        instructions:'You are a helpful AI assistant.\nReasoning strength: high.'}},ctx);
      await profiles.get(chat);await generation.settings(chat);await budget.settings(chat);return String(chat.id);
    },
    async submit(id:string,text:string){
      if(typeof text!=='string'||!text.trim()||text.length>4000)throw Error('Send 1–4,000 characters.');
      if(closing||active!==null||configuring)throw Error('The kernel already has a conversation generating or configuring. Wait or stop that conversation.');
      // Claim before the first await, including validation, so simultaneous callers cannot pass admission.
      active=Number(id);
      let admitted:{chat:Awaited<ReturnType<typeof conversation>>;id:string}|undefined;
      try{
        const work=await harness.inspect(ctx);
        if(work.tasks.length||work.submissions.length)throw Error('Saved work is still pending. Open its session and stop it before sending another request.');
        const chat=await conversation(id);
        const run=await budget.start(chat);admitted={chat,id:run.id};
        const submission=await profiles.submit(chat,text);
        trackSettlement((async()=>{let reason:AgentBudgetReason='complete';try{const result=await submission.wait(ctx);if(result.status!=='done')reason='failed';}catch(e){reason='failed';await host.report(chat.id,null,e);}finally{await finish(chat,run.id,reason);}})(),chat.id);
        return {accepted:true,submission:submission.id};
      }catch(e){try{if(admitted)await finish(admitted.chat,admitted.id,'failed');}finally{active=null;}throw e;}
    },
    async abort(id:string){const chat=await conversation(id);await budget.stop(chat,'aborted');await chat.abort(ctx);await host.settled(chat.id);const status=await budget.status(chat);if(status.run?.status==='running')await finish(chat,status.run.id,status.owned?'aborted':'recovered');},
    async budget(id:string){return budget.status(await conversation(id));},
    async setBudget(id:string,version:number,mode:AgentMode){
      if(closing||active!==null||configuring)throw Error('Wait for the run to settle before changing its budget.');
      configuring=true;try{const chat=await conversation(id);await budget.save(chat,version,mode);return budget.status(chat);}finally{configuring=false;}
    },
    async generation(id:string){return generation.settings(await conversation(id));},
    async setGeneration(id:string,version:number,sampling:boolean){
      if(closing||active!==null||configuring)throw Error('Wait for generation to settle before changing decoding.');
      return generation.save(await conversation(id),version,sampling);
    },
    async retry(id:string,target:number){
      if(closing||active!==null||configuring)throw Error('The kernel already has a conversation generating or configuring. Wait or stop that conversation.');
      active=Number(id);
      let admitted:{chat:Awaited<ReturnType<typeof conversation>>;id:string}|undefined;
      try{
        const work=await harness.inspect(ctx);
        if(work.tasks.length||work.submissions.length)throw Error('Saved work is still pending. Wait for it to settle.');
        const chat=await conversation(id),profile=await profiles.get(chat);
        if(profile.active?.content.tools?.length){if(!files)throw Error('Sandboxed tools unavailable.');await files.ready(profile.active.content.tools);}
        const run=await budget.start(chat);admitted={chat,id:run.id};
        const task=await generation.retry(chat,target);
        trackSettlement(finish(chat,run.id,'complete'),chat.id);
        return {accepted:true,task};
      }catch(e){try{if(admitted)await finish(admitted.chat,admitted.id,'failed');}finally{active=null;}throw e;}
    },
    async profile(id:string){return profiles.get(await conversation(id));},
    async saveProfile(id:string,version:number,content:unknown,origin?:ProfileOrigin){configurationFree();return profiles.save(await conversation(id),version,content,origin);},
    async restoreProfile(id:string,version:number,entry:number){
      configurationFree();
      if(!Number.isSafeInteger(entry)||entry<1)throw Error('Invalid profile entry.');
      return profiles.restore(await conversation(id),version,entry as EntryId);
    },
    async forkProfile(id:string,entry:number){
      configurationFree();
      if(closing||(await api.list()).length>=100)throw Error('Cannot create another conversation.');
      if(!Number.isSafeInteger(entry)||entry<1)throw Error('Invalid fork entry.');
      return String((await profiles.fork(await conversation(id),entry as EntryId)).id);
    },
    async readout(id:string,config:ReadoutConfig,assets:Assets){configurationFree();validateReadout(config,assets);const c=await conversation(id);await host.setReadout(c,config);return host.settings(c.id);},
    async steer(id:string,steer:Steer|null,assets:Assets){configurationFree();validateSteer(steer,assets);const c=await conversation(id);await host.setSteer(c,steer);return host.settings(c.id);},
    async bridge(id:string,send:(m:BridgeMessage)=>Promise<void>){
      const c=await conversation(id);await generation.settings(c);await budget.settings(c);const bridge=await openBridge(harness,host,c.id,ctx,send);
      let ended!:()=>void;const closed=new Promise<void>(resolve=>ended=resolve);
      const owned={snapshot:bridge.snapshot,closed,stop:async()=>{bridges.delete(owned);ended();await bridge.stop();}};
      bridges.add(owned);return owned;
    },
    async close(){closing=true;await budget.close();if(active!==null)await (await conversation(String(active))).abort(ctx);await files?.close();await Promise.allSettled([...settlements]);for(const b of bridges)await b.stop();await host.close();generation.close();await harness.close(ctx);}
  };return api;
}
export type Runtime=Awaited<ReturnType<typeof createRuntime>>;
const layers=(v:unknown,allowed:number[],limit:number):v is number[]=>Array.isArray(v)&&v.length>0&&v.length<=limit&&new Set(v).size===v.length&&v.every(l=>Number.isInteger(l)&&allowed.includes(l));
export function validateReadout(c:ReadoutConfig,a:Assets){
  if(!c||typeof c.enabled!=='boolean'||Object.keys(c).some(k=>!['enabled','emotion','workspace'].includes(k)))throw Error('Invalid readout settings.');
  if(!c.enabled){if(c.emotion!==null||c.workspace!==null)throw Error('Disabled readouts must have null settings.');return;}
  const d=a.directions.find(d=>d.id===c.emotion?.directions);
  if(!a.supported||!d||!c.emotion||Object.keys(c.emotion).some(k=>!['layers','directions'].includes(k))||!layers(c.emotion.layers,d.layers,3))throw Error('Unsupported emotion asset/layers (up to three).');
  if(c.workspace!==null){const w=c.workspace,l=a.lenses.find(l=>l.id===w?.lens);
    if(!l||Object.keys(w).some(k=>!['layers','lens','k'].includes(k))||!layers(w.layers,l.layers,4)||![1,2,4,8,16].includes(w.k))throw Error('Unsupported workspace settings.');}
}
export function validateSteer(s:Steer|null,a:Assets){
  if(s===null)return;
  const d=a.directions.find(d=>d.id===s?.directions);
  if(!a.supported||!d||!d.layers.includes(s.layer)||!d.names.includes(s.direction)||typeof s.strength!=='number'||!Number.isFinite(s.strength)||Math.abs(s.strength)>2
    ||Object.keys(s).some(k=>!['layer','directions','direction','strength'].includes(k)))throw Error('Invalid steering direction, layer or signed strength (−2 to +2).');
}
