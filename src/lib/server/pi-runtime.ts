import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {createModels} from '@earendil-works/pi-ai';
import {BACKGROUND_CONTEXT as ctx,createRegistry,Harness,openNodeSqliteStorage,type ConversationId,type EntryId,type Storage} from 'glimmer-agent/pi';
import {ProfileHost,profileExtension} from 'glimmer-agent/profile';
import type {ProfileOrigin,SandboxTool} from 'glimmer-agent/profile-model';
import {fileToolsExtension,type FileBroker} from 'glimmer-agent/file-tools';
import type {ToolAvailability} from './file-broker.ts';
import {ReadoutHost,readoutExtension,type ReadoutConfig,type Steer} from 'glimmer-agent/extension';
import {NOMOS_MODEL,NOMOS_PROVIDER,nomosProvider} from 'glimmer-agent/provider';
import {openBridge,type Bridge,type BridgeMessage} from 'glimmer-agent/bridge';
import type {Assets} from '../pi/model.ts';

export function checkedBase(value:string){
  const url=new URL(value);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||!['','/'].includes(url.pathname))throw Error('Invalid operator-configured kernel URL.');
  return url.origin;
}
export async function createRuntime(baseUrl:string, database:string|Storage,files?:FileBroker & {status():ToolAvailability;close():Promise<void>}){
  const fileExtension=files?fileToolsExtension(files):null;
  const base=checkedBase(baseUrl), host=new ReadoutHost(ctx), profiles=new ProfileHost(ctx,files?{ready:tools=>files.ready(tools),tools:fileExtension!.tools!}:undefined), models=createModels(), registry=createRegistry();
  models.setProvider(nomosProvider({baseUrl:base+'/v1',requests:host.requests,maxTokens:2048}));
  registry.install(readoutExtension(host));
  registry.install(profileExtension(profiles));
  if(fileExtension)registry.install(fileExtension);
  if(typeof database==='string')await mkdir(path.dirname(database),{recursive:true,mode:0o700});
  const storage=typeof database==='string'?await openNodeSqliteStorage(database):database;
  const harness=await Harness.open(storage,
    {models,registry,settings:{retry:{enabled:false},compaction:{enabled:false},stream:{maxRetries:0,timeoutMs:300000}}},ctx);
  host.attach(harness);
  profiles.attach(harness);
  let active:number|null=null,closing=false;
  const bridges=new Set<Bridge>();
  async function conversation(id:string){
    if(!/^[1-9]\d{0,14}$/.test(id)||!Number.isSafeInteger(Number(id)))throw Error('Invalid conversation ID.');
    const chat=await harness.conversation(Number(id) as ConversationId,ctx);
    if(!chat)throw Error('Conversation not found.');return chat;
  }
  const api={host,harness,
    busy(){return active!==null;},
    toolStatus(){return files?.status()??{available:false,reason:'No sandbox broker configured.',policy:'glimmer.files/bwrap-1'};},
    async checkTools(tools:readonly SandboxTool[]=[]){if(!files)throw Error('No sandbox broker configured.');await files.ready(tools);return files.status();},
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
      await profiles.get(chat);return String(chat.id);
    },
    async submit(id:string,text:string){
      if(typeof text!=='string'||!text.trim()||text.length>4000)throw Error('Send 1–4,000 characters.');
      if(closing||active!==null)throw Error('The kernel already has a conversation generating. Wait or stop that conversation.');
      // Claim before the first await, including validation, so simultaneous callers cannot pass admission.
      active=Number(id);
      try{
        const work=await harness.inspect(ctx);
        if(work.tasks.length||work.submissions.length)throw Error('Saved work is still pending. Open its session and stop it before sending another request.');
        const chat=await conversation(id);
        const submission=await profiles.submit(chat,text);
        void submission.wait(ctx).catch(e=>host.report(chat.id,null,e)).finally(()=>{active=null;});
        return {accepted:true,submission:submission.id};
      }catch(e){active=null;throw e;}
    },
    async abort(id:string){const chat=await conversation(id);await chat.abort(ctx);await host.settled(chat.id);},
    async profile(id:string){return profiles.get(await conversation(id));},
    async saveProfile(id:string,version:number,content:unknown,origin?:ProfileOrigin){return profiles.save(await conversation(id),version,content,origin);},
    async restoreProfile(id:string,version:number,entry:number){
      if(!Number.isSafeInteger(entry)||entry<1)throw Error('Invalid profile entry.');
      return profiles.restore(await conversation(id),version,entry as EntryId);
    },
    async forkProfile(id:string,entry:number){
      if(closing||(await api.list()).length>=100)throw Error('Cannot create another conversation.');
      if(!Number.isSafeInteger(entry)||entry<1)throw Error('Invalid fork entry.');
      return String((await profiles.fork(await conversation(id),entry as EntryId)).id);
    },
    async readout(id:string,config:ReadoutConfig,assets:Assets){validateReadout(config,assets);const c=await conversation(id);await host.setReadout(c,config);return host.settings(c.id);},
    async steer(id:string,steer:Steer|null,assets:Assets){validateSteer(steer,assets);const c=await conversation(id);await host.setSteer(c,steer);return host.settings(c.id);},
    async bridge(id:string,send:(m:BridgeMessage)=>Promise<void>){
      const c=await conversation(id);const bridge=await openBridge(harness,host,c.id,ctx,send);bridges.add(bridge);
      return {snapshot:bridge.snapshot,stop:async()=>{bridges.delete(bridge);await bridge.stop();}};
    },
    async close(){closing=true;if(active!==null)await (await conversation(String(active))).abort(ctx);await files?.close();for(const b of bridges)await b.stop();await host.close();await harness.close(ctx);}
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
