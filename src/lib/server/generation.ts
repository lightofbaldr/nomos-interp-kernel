/** App decoding policy and capped-reply retry. All metadata stays outside model context. */
import type {Message,StreamOptions} from '@earendil-works/pi-ai';
import type {Provider} from '@earendil-works/pi-ai/models';
import {defineDoc,defineEntry,defineExtension,GenerationTask,hook,InboxDoc,LiveDoc,ProviderDoc,
  type Context,type Conversation,type EntryId,type HarnessT,type HookApi} from 'glimmer-agent/pi';
import {ReadoutStateDoc} from 'glimmer-agent/extension';
import {cappedReasoningOnly,generationParameters,type GenerationSettings,type GenerationUsed} from '../pi/generation.ts';

const SettingsDoc=defineDoc<GenerationSettings>({kind:'glimmer.generation',version:1,scope:'conversation',history:'rewindable',fork:'asOf',initial:()=>({version:0,sampling:false})});
const RequestDoc=defineDoc<{key:string}>({kind:'glimmer.generation.request',version:1,scope:'conversation',history:'rewindable',fork:'initial',initial:()=>({key:''})});
const Changed=defineEntry<GenerationSettings>('glimmer.generation.changed');
const Used=defineEntry<GenerationUsed>('glimmer.generation.used');
const Retried=defineEntry<{target:number;task:number;reason:string}>('glimmer.retry');
type Prepared={last:Message|undefined;used:GenerationUsed;remove():void};

export class GenerationHost {
  #harness!:HarnessT;
  #prepared=new Map<string,Prepared>();
  private context:Context;
  constructor(context:Context){this.context=context;}
  attach(harness:HarnessT){this.#harness=harness;}
  async settings(c:Conversation){
    return c.commit(async tx=>{
      const state=await tx.doc(SettingsDoc,c.id);
      if(state.version===0){state.version=1;await tx.appendEntry(Changed,c.id,{data:{...state}});}
      return {...state};
    },this.context);
  }
  async save(c:Conversation,version:number,sampling:boolean){
    generationParameters(sampling);
    return c.commit(async tx=>{
      const live=await tx.doc(LiveDoc,c.id),inbox=await tx.doc(InboxDoc,c.id),state=await tx.doc(SettingsDoc,c.id);
      if(live.run||live.compactions?.length||inbox.items.length)throw Error('Wait for the conversation to settle before changing decoding.');
      if(!Number.isSafeInteger(version)||version!==state.version)throw Error('Decoding changed. Reload before saving.');
      state.version++;state.sampling=sampling;
      const settings={...state};await tx.appendEntry(Changed,c.id,{data:settings});return settings;
    },this.context);
  }
  async prepare(api:HookApi,messages:readonly Message[],context:Context){
    const task=await this.#harness.getTask(api.taskId,context),checkpoint=task?.state.checkpoint;
    if(!checkpoint||typeof checkpoint!=='object'||Array.isArray(checkpoint)||checkpoint.phase!=='request'
      ||typeof checkpoint.cutoff!=='number'||typeof checkpoint.attempt!=='number')throw Error('Cannot attribute decoding to this request.');
    const saved=await api.snapshotAsOf(SettingsDoc,api.conversationId,checkpoint.cutoff as EntryId,context);
    const provider=await api.snapshot(ProviderDoc,api.conversationId,context);
    if(!provider?.sessionId)throw Error('No Pi provider session for decoding.');
    const readout=await api.snapshot(ReadoutStateDoc,api.conversationId,context);
    const used:GenerationUsed={task:api.taskId,attempt:checkpoint.attempt,cutoff:checkpoint.cutoff,
      request:readout?.open?.task===api.taskId?readout.open.request:null,
      mode:saved?.sampling?'sampled':'greedy',parameters:generationParameters(saved?.sampling??false)};
    const c=await this.#harness.conversation(api.conversationId,context);if(!c)throw Error('Conversation unavailable.');
    await c.commit(async tx=>{
      const state=await tx.doc(RequestDoc,c.id),key=JSON.stringify(used);
      if(state.key===key)return;
      state.key=key;await tx.appendEntry(Used,c.id,{data:used});
    },context);
    const session=provider.sessionId;
    this.#prepared.get(session)?.remove();
    const remove=()=>{context.abortSignal?.removeEventListener('abort',remove);if(this.#prepared.get(session)===prepared)this.#prepared.delete(session);};
    const prepared:Prepared={last:messages.at(-1),used,remove};
    if(context.abortSignal?.aborted)throw Error('Decoding preparation aborted.');
    this.#prepared.set(session,prepared);context.abortSignal?.addEventListener('abort',remove,{once:true});
  }
  #options<O extends StreamOptions>(messages:readonly Message[],options:O|undefined):O {
    const prepared=options?.sessionId?this.#prepared.get(options.sessionId):undefined;
    if(!prepared||prepared.last!==messages.at(-1))throw Error('No matching, attributed decoding request.');
    prepared.remove();
    const onPayload:NonNullable<StreamOptions['onPayload']>=async(payload,model)=>{
      const original=await options?.onPayload?.(payload,model)??payload;
      if(!original||typeof original!=='object'||Array.isArray(original))throw Error('Invalid Nomos request payload.');
      return {...original,...prepared.used.parameters};
    };
    return {...options,onPayload} as O;
  }
  provider(base:Provider):Provider {
    return {...base,stream:(model,context,options)=>base.stream(model,context,this.#options(context.messages,options)),
      streamSimple:(model,context,options)=>base.streamSimple(model,context,this.#options(context.messages,options))};
  }
  extension(){return defineExtension({name:'glimmer-generation',hooks:[hook(GenerationTask,{
    beforeRequest:async(request,api,context)=>{await this.prepare(api,request.messages,context);return undefined;}
  })]});}
  /** Native Pi 1.3.0 startRun shape, using public task/doc APIs. No synthetic input or transcript deletion. */
  async retry(c:Conversation,target:number){
    if(!Number.isSafeInteger(target)||target<1)throw Error('Invalid retry target entry.');
    const task=await c.commit(async tx=>{
      const live=await tx.doc(LiveDoc,c.id),inbox=await tx.doc(InboxDoc,c.id);
      if(live.run||live.compactions?.length||inbox.items.length)throw Error('Wait for the conversation to settle before Retry.');
      const head=await tx.latestHeadMarker(c.id);
      let page=await tx.scanEntries({conversationId:c.id,order:'descending'},64),last;
      search:while(true){
        for(const e of page.items){
          if(head&&e.id<head.head)break search;
          if(e.kind==='glimmer.retry'&&(e.data as {target?:number})?.target===target)throw Error('This attempt was already retried.');
          if(e.edits?.some(edit=>edit.target===target))throw Error('The last reply was already edited out of model context.');
          if(e.model?.length){last=e;break search;}
        }
        if(!page.next)break;page=await tx.scanEntries({conversationId:c.id,order:'descending'},64,page.next);
      }
      if(!last||last.id!==target)throw Error('Retry must target the last reply; the conversation changed.');
      if(last.model?.length!==1||!cappedReasoningOnly(last.model[0]))throw Error('Only a capped, reasoning-only reply is eligible for Retry.');
      const taskId=await tx.createTask(GenerationTask,{}, {ownership:{kind:'conversation'},conversationId:c.id});
      await tx.appendEntry(Retried,c.id,{data:{target,task:taskId,reason:'reply_limit'},edits:[{target:last.id,action:'omit'}]});
      live.run={taskId,inputs:[]};
      return taskId;
    },this.context);
    this.#harness.resume();return task;
  }
  close(){for(const p of this.#prepared.values())p.remove();}
}
