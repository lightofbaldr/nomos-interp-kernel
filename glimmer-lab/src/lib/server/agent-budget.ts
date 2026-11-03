/** Server-owned admission, separate from Pi's per-request SDK timeout.
 * Integrate in this order: start -> submit/retry -> await settlement -> finish.
 * Install extension() before readout hooks; wrap every broker execute with
 * executeTool(). Merge signal() with Pi's provider signal through the complete
 * stream. The abort callback joins Pi/readout settlement; it must not await this
 * host's finish() or close(). These entries contain metadata only, never model messages. */
import {randomUUID} from 'node:crypto';
import {defineDoc,defineEntry,defineExtension,GenerationTask,hook,InboxDoc,LiveDoc,
  type Context,type Conversation,type ConversationId,type HarnessT,type HookApi,type Tx} from 'glimmer-agent/pi';
import {agentMode,budgetLimits,type AgentBudgetAdmission,type AgentBudgetReason,type AgentBudgetRun,
  type AgentBudgetSettings,type AgentBudgetStatus,type AgentMode} from '../agent/budget.ts';

export const AgentBudgetSettingsDoc=defineDoc<AgentBudgetSettings>({kind:'glimmer.agent.settings',version:1,
  scope:'conversation',history:'rewindable',fork:'asOf',initial:()=>({version:0,mode:'chat'})});
// Latest, not rewindable: restoring a profile must never refund a running budget.
export const AgentBudgetRunDoc=defineDoc<{run:AgentBudgetRun|null}>({kind:'glimmer.agent.run',version:1,
  scope:'conversation',history:'latest',fork:'initial',initial:()=>({run:null})});
const Changed=defineEntry<AgentBudgetSettings>('glimmer.agent.changed');
const Started=defineEntry<AgentBudgetRun>('glimmer.agent.started');
const Request=defineEntry<AgentBudgetAdmission>('glimmer.agent.request');
type ToolAdmission={runId:string;invocationId:string;task:number;tool:string;toolCall:number;at:number};
const Tool=defineEntry<ToolAdmission>('glimmer.agent.tool');
const ToolEnded=defineEntry<ToolAdmission&{endedAt:number;outcome:'returned'|'threw'|'aborted';ok:boolean|null}>('glimmer.agent.tool-ended');
const Stopped=defineEntry<{runId:string;reason:AgentBudgetReason;at:number}>('glimmer.agent.stopped');
const Finished=defineEntry<AgentBudgetRun>('glimmer.agent.finished');

type Owner={conversation:Conversation;id:string;controller:AbortController;timer:ReturnType<typeof setTimeout>|undefined;
  seen:Set<string>;admission:AgentBudgetAdmission|null;reason:AgentBudgetReason|null;tools:Set<Promise<unknown>>;abortWork?:Promise<void>};
type Options={abort:(conversationId:number,runId:string,reason:AgentBudgetReason)=>Promise<void>;
  /** Trusted clock seams for offline tests; never supplied by an HTTP client. */
  now?:()=>number;schedule?:(callback:()=>void,ms:number)=>ReturnType<typeof setTimeout>;
  cancel?:(timer:ReturnType<typeof setTimeout>)=>void};
// Transaction documents may be proxies; structuredClone cannot clone drafts.
const copy=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
const failure=(reason:string)=>Error(`Agent budget refused: ${reason}. Stop or settle the run before another admission.`);
async function piBusy(tx:Tx,id:ConversationId){
  const live=await tx.doc(LiveDoc,id),inbox=await tx.doc(InboxDoc,id);
  return Boolean(live.run||live.generation||live.tools?.length||live.nestedTools?.length||live.compactions?.length||inbox.items.length);
}

export class AgentBudgetHost {
  #harness!:HarnessT;
  #owners=new Map<number,Owner>();
  #starting=new Set<number>();
  #starts=new Set<Promise<unknown>>();
  #background=new Set<Promise<unknown>>();
  #closing=false;
  #closed:Promise<void>|undefined;
  #context:Context;
  #options:Options;
  constructor(context:Context,options:Options){this.#context=context;this.#options=options;}
  attach(harness:HarnessT){this.#harness=harness;}
  #now(){const now=(this.#options.now??Date.now)();if(!Number.isSafeInteger(now)||now<0)throw Error('Invalid agent budget clock.');return now;}
  #cancel(owner:Owner){if(owner.timer!==undefined){(this.#options.cancel??clearTimeout)(owner.timer);owner.timer=undefined;}}
  #track(work:Promise<unknown>){this.#background.add(work);void work.finally(()=>this.#background.delete(work)).catch(()=>{});}
  #abort(owner:Owner,reason:AgentBudgetReason){
    owner.reason??=reason;this.#cancel(owner);
    if(!owner.controller.signal.aborted)owner.controller.abort(failure(owner.reason));
    if(owner.abortWork)return;
    // Do not await abort inside a GenerationTask hook or a tool invocation: Pi
    // must first unwind that invocation before its abort can finish joining it.
    owner.abortWork=Promise.resolve().then(async()=>{
      if(this.#owners.get(owner.conversation.id)===owner)await this.#options.abort(owner.conversation.id,owner.id,owner.reason!);
    });
    this.#track(owner.abortWork);
  }
  async #recordStop(c:Conversation,runId:string,reason:AgentBudgetReason){
    await c.commit(async tx=>{
      const run=(await tx.doc(AgentBudgetRunDoc,c.id)).run;
      if(!run||run.id!==runId||run.status!=='running'||run.stopReason)return;
      run.stopReason=reason;await tx.appendEntry(Stopped,c.id,{data:{runId,reason,at:this.#now()}});
    },this.#context);
  }
  #expire(owner:Owner){
    if(this.#owners.get(owner.conversation.id)!==owner||owner.reason)return;
    // The timer fires even if the system wall clock moved backwards.
    this.#abort(owner,'wall_time');
    this.#track(this.#recordStop(owner.conversation,owner.id,'wall_time'));
  }
  async settings(c:Conversation):Promise<AgentBudgetSettings>{
    return c.commit(async tx=>{
      const settings=await tx.doc(AgentBudgetSettingsDoc,c.id);
      if(settings.version===0){settings.version=1;await tx.appendEntry(Changed,c.id,{data:copy(settings)});}
      return copy(settings);
    },this.#context);
  }
  async save(c:Conversation,version:number,mode:AgentMode):Promise<AgentBudgetSettings>{
    agentMode(mode);
    if(this.#closing||this.#starting.has(c.id)||this.#owners.has(c.id))throw failure('run is active');
    return c.commit(async tx=>{
      const settings=await tx.doc(AgentBudgetSettingsDoc,c.id),run=(await tx.doc(AgentBudgetRunDoc,c.id)).run;
      if(await piBusy(tx,c.id)||run?.status==='running')throw failure('saved work is active');
      if(!Number.isSafeInteger(version)||version!==settings.version)throw Error('Agent settings changed. Reload before saving.');
      settings.version++;settings.mode=mode;await tx.appendEntry(Changed,c.id,{data:copy(settings)});return copy(settings);
    },this.#context);
  }
  async status(c:Conversation):Promise<AgentBudgetStatus>{
    const settings=await this.#harness.snapshot(AgentBudgetSettingsDoc,c.id,this.#context);
    const stored=await this.#harness.snapshot(AgentBudgetRunDoc,c.id,this.#context);
    const run=stored?.run??null,owner=this.#owners.get(c.id);
    return {settings:copy(settings??{version:0,mode:'chat'}),run:copy(run),owned:Boolean(owner&&owner.id===run?.id)};
  }
  async start(c:Conversation):Promise<AgentBudgetRun>{
    if(this.#closing||this.#starting.has(c.id)||this.#owners.has(c.id))throw failure('run is already admitted or host is closing');
    this.#starting.add(c.id);
    const work=this.#start(c);this.#starts.add(work);
    try{return await work;}finally{this.#starts.delete(work);this.#starting.delete(c.id);}
  }
  async #start(c:Conversation){
    const run=await c.commit(async tx=>{
      const state=await tx.doc(AgentBudgetRunDoc,c.id),settings=await tx.doc(AgentBudgetSettingsDoc,c.id);
      if(state.run?.status==='running')throw failure('persisted run requires explicit settlement; recovery cannot rearm it');
      if(await piBusy(tx,c.id))throw failure('Pi is not idle');
      const mode=agentMode(settings.mode),limits=budgetLimits(mode),startedAt=this.#now();
      const run:AgentBudgetRun={id:randomUUID(),mode,settingsVersion:settings.version,limits,startedAt,
        deadlineAt:limits.wallMs===null?null:startedAt+limits.wallMs,endedAt:null,status:'running',stopReason:null,
        modelRequests:0,toolCalls:0,lastRequest:null};
      state.run=run;await tx.appendEntry(Started,c.id,{data:run});return copy(run);
    },this.#context);
    const owner:Owner={conversation:c,id:run.id,controller:new AbortController(),timer:undefined,seen:new Set(),admission:null,reason:null,tools:new Set()};
    this.#owners.set(c.id,owner);
    if(run.deadlineAt!==null){
      const remaining=Math.max(0,run.deadlineAt-this.#now());
      owner.timer=(this.#options.schedule??setTimeout)(()=>this.#expire(owner),remaining);
      owner.timer.unref?.();
    }
    if(this.#closing){this.#abort(owner,'closed');await this.#recordStop(c,run.id,'closed');throw failure('host is closing');}
    return run;
  }
  #owner(id:number):Owner {
    const owner=this.#owners.get(id);
    if(this.#closing||!owner)throw failure('no admitted runtime owner (recovery never grants fresh work)');
    if(owner.reason)throw failure(owner.reason);
    owner.controller.signal.throwIfAborted();return owner;
  }
  signal(conversationId:number,runId?:string):AbortSignal {
    const owner=this.#owner(conversationId);if(runId!==undefined&&owner.id!==runId)throw failure('run identity changed');
    return owner.controller.signal;
  }
  requestLimit(conversationId:number,taskId:number):AgentBudgetAdmission {
    const owner=this.#owner(conversationId),admission=owner.admission;
    if(!admission||admission.task!==taskId)throw failure('request has not been admitted');
    if(admission.deadlineAt!==null&&this.#now()>=admission.deadlineAt){this.#expire(owner);throw failure('wall_time');}
    return copy(admission);
  }
  async prepare(api:HookApi,context:Context){
    const owner=this.#owner(api.conversationId);context.abortSignal?.throwIfAborted();
    const task=await this.#harness.getTask(api.taskId,context),checkpoint=task?.state.checkpoint;
    if(!checkpoint||typeof checkpoint!=='object'||Array.isArray(checkpoint)||checkpoint.phase!=='request'
      ||!Number.isSafeInteger(checkpoint.attempt)||!Number.isSafeInteger(checkpoint.cutoff))throw failure('invalid Pi request checkpoint');
    const attempt=checkpoint.attempt as number,cutoff=checkpoint.cutoff as number,key=`${api.taskId}:${attempt}:${cutoff}`;
    const result=await owner.conversation.commit(async tx=>{
      const run=(await tx.doc(AgentBudgetRunDoc,api.conversationId)).run;
      if(!run||run.id!==owner.id||run.status!=='running')throw failure('run identity changed');
      const reason=run.stopReason??owner.reason??(run.deadlineAt!==null&&this.#now()>=run.deadlineAt?'wall_time':
        run.limits.modelRequests!==null&&run.modelRequests>=run.limits.modelRequests?'model_requests':null);
      if(reason){if(!run.stopReason){run.stopReason=reason;await tx.appendEntry(Stopped,api.conversationId,{data:{runId:run.id,reason,at:this.#now()}});}return {reason,admission:null};}
      if(owner.seen.has(key)||run.lastRequest?.key===key)throw failure('request was already admitted; replay is not a new allowance');
      const admission:AgentBudgetAdmission={runId:run.id,mode:run.mode,outputTokens:run.limits.outputTokens,modelRequest:++run.modelRequests,
        toolCalls:run.toolCalls,startedAt:run.startedAt,deadlineAt:run.deadlineAt,limits:copy(run.limits),task:api.taskId,attempt,cutoff,key};
      run.lastRequest=admission;await tx.appendEntry(Request,api.conversationId,{data:admission});return {reason:null,admission:copy(admission)};
    },this.#context);
    if(result.reason){this.#abort(owner,result.reason);throw failure(result.reason);}
    owner.seen.add(key);owner.admission=result.admission;owner.controller.signal.throwIfAborted();
  }
  extension(){return defineExtension({name:'glimmer-agent-budget',hooks:[hook(GenerationTask,{
    beforeRequest:async(_request,api,context)=>{await this.prepare(api,context);return undefined;}
  })]});}
  async executeTool<T>(conversationId:number,tool:string,execute:(signal:AbortSignal)=>Promise<T>,callerSignal?:AbortSignal):Promise<T>{
    const owner=this.#owner(conversationId);
    if(!/^[a-z][a-z0-9_]{0,63}$/.test(tool))throw Error('Invalid budgeted tool name.');
    const work=this.#executeTool(owner,tool,execute,callerSignal);owner.tools.add(work);
    try{return await work;}finally{owner.tools.delete(work);}
  }
  async #executeTool<T>(owner:Owner,tool:string,execute:(signal:AbortSignal)=>Promise<T>,callerSignal?:AbortSignal):Promise<T>{
    const c=owner.conversation;
    const result=await c.commit(async tx=>{
      const run=(await tx.doc(AgentBudgetRunDoc,c.id)).run;
      if(!run||run.id!==owner.id||run.status!=='running'||!run.lastRequest)throw failure('tool has no admitted model request');
      const reason=run.stopReason??owner.reason??(run.deadlineAt!==null&&this.#now()>=run.deadlineAt?'wall_time':
        run.limits.toolCalls!==null&&run.toolCalls>=run.limits.toolCalls?'tool_calls':null);
      if(reason){if(!run.stopReason){run.stopReason=reason;await tx.appendEntry(Stopped,c.id,{data:{runId:run.id,reason,at:this.#now()}});}return {reason,admission:null};}
      const admission:ToolAdmission={runId:run.id,invocationId:randomUUID(),task:run.lastRequest.task,tool,toolCall:++run.toolCalls,at:this.#now()};
      await tx.appendEntry(Tool,c.id,{data:admission});return {reason:null,admission};
    },this.#context);
    if(result.reason){this.#abort(owner,result.reason);throw failure(result.reason);}
    const signal=callerSignal?AbortSignal.any([callerSignal,owner.controller.signal]):owner.controller.signal;
    let outcome:'returned'|'threw'|'aborted'='threw',ok:boolean|null=null;
    try{
      signal.throwIfAborted();const value=await execute(signal);signal.throwIfAborted();outcome='returned';
      if(value&&typeof value==='object'&&'ok' in value&&typeof value.ok==='boolean')ok=value.ok;
      return value;
    }finally{
      if(signal.aborted)outcome='aborted';
      await c.commit(async tx=>{await tx.appendEntry(ToolEnded,c.id,{data:{...result.admission!,endedAt:this.#now(),outcome,ok}});},this.#context);
    }
  }
  /** Mark explicit stop before Pi settlement. The callback is launched, not
   * awaited, so callers can join Pi themselves without recursive abort waits. */
  async stop(c:Conversation,reason:AgentBudgetReason='aborted'):Promise<void>{
    if(!['aborted','failed','model_requests','tool_calls','wall_time','recovered','closed'].includes(reason))throw Error('Invalid agent budget stop reason.');
    const owner=this.#owners.get(c.id);
    // Pin the reason before the first await. A concurrent success-shaped Pi
    // completion must not turn an already requested stop into "complete".
    if(owner){owner.reason??=reason;this.#cancel(owner);owner.controller.abort(failure(owner.reason));}
    const run=(await this.#harness.snapshot(AgentBudgetRunDoc,c.id,this.#context))?.run;
    if(run?.status==='running')await this.#recordStop(c,run.id,owner?.reason??reason);
    if(owner)this.#abort(owner,owner.reason!);
  }
  async finish(c:Conversation,runId:string,reason:AgentBudgetReason='complete'):Promise<AgentBudgetRun>{
    if(!['complete','aborted','failed','model_requests','tool_calls','wall_time','recovered','closed'].includes(reason))throw Error('Invalid agent budget stop reason.');
    const owner=this.#owners.get(c.id);
    if(owner&&owner.id!==runId)throw failure('run identity changed');
    if(owner?.tools.size)throw failure('tool invocation has not settled');
    // A late abort callback must not reach a newly admitted run after this one
    // is released. The callback joins Pi only; it never recursively finishes us.
    await owner?.abortWork;
    const run=await c.commit(async tx=>{
      const run=(await tx.doc(AgentBudgetRunDoc,c.id)).run;
      if(!run||run.id!==runId)throw failure('run identity changed');
      if(run.status==='finished')return copy(run);
      if(await piBusy(tx,c.id))throw failure('Pi has not settled');
      run.stopReason=run.stopReason??owner?.reason??(!owner?'recovered':reason);
      run.status='finished';run.endedAt=this.#now();await tx.appendEntry(Finished,c.id,{data:copy(run)});return copy(run);
    },this.#context);
    if(owner){this.#cancel(owner);owner.controller.abort(failure('run finished'));if(this.#owners.get(c.id)===owner)this.#owners.delete(c.id);}
    return run;
  }
  close():Promise<void>{return this.#closed??=this.#close();}
  async #close(){
    this.#closing=true;await Promise.allSettled([...this.#starts]);
    const owners=[...this.#owners.values()];
    for(const owner of owners){this.#abort(owner,'closed');this.#track(this.#recordStop(owner.conversation,owner.id,'closed'));}
    while(this.#background.size)await Promise.allSettled([...this.#background]);
    await Promise.allSettled(owners.flatMap(owner=>[...owner.tools]));
    // Leave the persisted record running/stopped unless the runtime explicitly
    // finishes after Pi settlement. A reopened host cannot rearm these records.
    for(const owner of owners)this.#cancel(owner);
    this.#owners.clear();
  }
}
