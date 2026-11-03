import assert from 'node:assert/strict';
import http from 'node:http';
import {rm} from 'node:fs/promises';
import {test} from 'node:test';
import {createModels} from '@earendil-works/pi-ai';
import {BACKGROUND_CONTEXT as ctx,createRegistry,Harness,MemoryStorage,defineExtension,GenerationTask,hook,
  type Conversation,type HarnessT,type HookApi} from 'glimmer-agent/pi';
import {nomosProvider,ReadoutRequests,NOMOS_MODEL,NOMOS_PROVIDER} from 'glimmer-agent/provider';
import {fileToolsExtension} from 'glimmer-agent/file-tools';
import {AgentBudgetHost} from '../../src/lib/server/agent-budget.ts';
import {AGENT_BUDGET,CHAT_OUTPUT_TOKENS,agentMode,budgetLimits,type AgentBudgetAdmission} from '../../src/lib/agent/budget.ts';
import {fileKernel} from '../files/fixture.ts';
import {until} from '../live/fixture.ts';

function clock(){
  let now=1000,next=0;const timers=new Map<number,{at:number;fn:()=>void}>(),callbacks:(()=>void)[]=[];
  return {now:()=>now,size:()=>timers.size,callbacks:()=>callbacks,
    schedule(fn:()=>void,ms:number){const id=++next;timers.set(id,{at:now+ms,fn});callbacks.push(fn);return {id,unref(){}} as unknown as ReturnType<typeof setTimeout>;},
    cancel(timer:ReturnType<typeof setTimeout>){timers.delete((timer as unknown as {id:number}).id);},
    advance(ms:number){now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}}
  };
}

/** Unit seam: real Pi conversation documents/MemoryStorage, synthetic request
 * checkpoints. A separate test below exercises actual successor GenerationTasks. */
async function fixture(agent=true){
  const time=clock(),storage=new MemoryStorage(),registry=createRegistry(),aborts:{id:number;run:string;reason:string}[]=[];
  let harness:HarnessT;
  const options={...time,abort:async(id:number,run:string,reason:any)=>{aborts.push({id,run,reason});await (await harness.conversation(id as any,ctx))?.abort(ctx);}};
  const host=new AgentBudgetHost(ctx,options);registry.install(host.extension());
  harness=await Harness.open(storage,{models:createModels(),registry,settings:{retry:{enabled:false},compaction:{enabled:false}}},ctx);
  const c=await harness.createConversation({ownership:{kind:'ownerless'},agent:{instructions:'Budget fixture.'}},ctx);
  let checkpoint={phase:'request',attempt:1,cutoff:1};
  const attach=(target:AgentBudgetHost)=>target.attach({
    snapshot:harness.snapshot.bind(harness),conversation:harness.conversation.bind(harness),
    getTask:async()=>({state:{checkpoint}})
  } as unknown as HarnessT);
  attach(host);const initial=await host.settings(c);if(agent)await host.save(c,initial.version,'agent');
  return {host,c,harness,storage,time,aborts,options,attach,
    async request(task:number,attempt=1,cutoff=task){checkpoint={phase:'request',attempt,cutoff};await host.prepare({conversationId:c.id,taskId:task} as HookApi,ctx);return host.requestLimit(c.id,task);},
    async entries(){return (await c.entries({order:'ascending'},500,undefined,ctx)).items;},
    async close(){await host.close();await harness.close(ctx);}
  };
}

test('approved preset is bounded; default chat preserves 6144 and settings cannot reset active or finished counts',async()=>{
  assert.deepEqual(AGENT_BUDGET,{outputTokens:8192,modelRequests:32,toolCalls:64,wallMs:1200000});
  assert.equal(CHAT_OUTPUT_TOKENS,6144);assert.equal(budgetLimits('chat').wallMs,null);
  for(const bad of [null,undefined,true,1,{},'Agent','',[]])assert.throws(()=>agentMode(bad),/chat or agent/);
  const f=await fixture(false);
  try{
    const initial=await f.host.status(f.c);assert.equal(initial.settings.mode,'chat');assert.equal(initial.run,null);
    const admission=await Promise.allSettled([f.host.start(f.c),f.host.start(f.c)]);
    assert.equal(admission.filter(result=>result.status==='fulfilled').length,1);
    assert.equal(admission.filter(result=>result.status==='rejected').length,1);
    const run=(admission.find(result=>result.status==='fulfilled') as PromiseFulfilledResult<Awaited<ReturnType<typeof f.host.start>>>).value;
    assert.equal(f.time.size(),0);assert.equal(run.deadlineAt,null);
    const competing=await Promise.allSettled([f.host.start(f.c),f.host.start(f.c)]);assert.ok(competing.every(result=>result.status==='rejected'));
    for(let task=1;task<=33;task++)assert.equal((await f.request(task)).outputTokens,6144);
    await assert.rejects(f.host.save(f.c,initial.settings.version,'agent'),/active/);
    await f.host.finish(f.c,run.id);const before=(await f.host.status(f.c)).run;
    const saved=await f.host.save(f.c,initial.settings.version,'agent');assert.equal(saved.mode,'agent');
    assert.deepEqual((await f.host.status(f.c)).run,before);assert.equal(before?.modelRequests,33);
    await assert.rejects(f.host.save(f.c,initial.settings.version,'chat'),/changed/);
    await assert.rejects(f.host.save(f.c,saved.version,true as any),/chat or agent/);
    const next=await f.host.start(f.c);assert.notEqual(next.id,run.id);assert.equal(next.limits.outputTokens,8192);
    assert.equal(next.modelRequests,0);assert.equal(f.time.size(),1);await f.host.finish(f.c,next.id);assert.equal(f.time.size(),0);
  }finally{await f.close();}
});

test('model admissions are exactly once, durable across successor tasks, and the 33rd request never receives a permit',async()=>{
  const f=await fixture();
  try{
    const run=await f.host.start(f.c);assert.equal(f.time.size(),1);
    for(let task=1;task<=32;task++){
      const permit=await f.request(task);assert.equal(permit.runId,run.id);assert.equal(permit.modelRequest,task);assert.equal(permit.outputTokens,8192);
    }
    await assert.rejects(f.request(33),/model_requests/);
    const state=await f.host.status(f.c);assert.equal(state.run?.modelRequests,32);assert.equal(state.run?.stopReason,'model_requests');
    assert.throws(()=>f.host.requestLimit(f.c.id,33),/model_requests/);
    await until(()=>f.aborts.length===1);assert.equal(f.aborts[0].run,run.id);
    const entries=await f.entries(),requests=entries.filter(entry=>entry.kind==='glimmer.agent.request');
    assert.equal(requests.length,32);assert.equal(new Set(requests.map(entry=>(entry.data as any).task)).size,32);
    assert.ok(entries.filter(entry=>entry.kind.startsWith('glimmer.agent.')).every(entry=>!entry.model&&!entry.edits&&!entry.head));
    assert.doesNotMatch(JSON.stringify((await f.c.context(ctx)).messages),/glimmer.agent|modelRequests|toolCalls|1200000/);
    await f.host.finish(f.c,run.id);assert.equal((await f.host.status(f.c)).run?.stopReason,'model_requests');
  }finally{await f.close();}
});

test('duplicate request checkpoints refuse replay instead of spending or refunding another allowance',async()=>{
  const f=await fixture();
  try{
    const run=await f.host.start(f.c);await f.request(10);await f.request(11);
    await assert.rejects(f.request(10),/already admitted/);assert.equal((await f.host.status(f.c)).run?.modelRequests,2);
    await f.host.finish(f.c,run.id);
    assert.throws(()=>f.host.requestLimit(f.c.id,11),/no admitted runtime owner/);
  }finally{await f.close();}
});

test('every tool invocation is charged before execution, including failures and multiple calls from one request',async()=>{
  const f=await fixture();let actual=0;
  try{
    const run=await f.host.start(f.c);await f.request(1);
    await assert.rejects(f.host.executeTool(f.c.id,'write',async()=>{actual++;throw Error('Synthetic failure');}),/Synthetic failure/);
    const calls=await Promise.allSettled(Array.from({length:64},()=>f.host.executeTool(f.c.id,'read',async()=>{actual++;return {ok:false};})));
    assert.equal(actual,64);assert.equal(calls.filter(call=>call.status==='rejected').length>=1,true);
    await until(()=>f.aborts.length===1);
    const state=await f.host.status(f.c);assert.equal(state.run?.modelRequests,1);assert.equal(state.run?.toolCalls,64);assert.equal(state.run?.stopReason,'tool_calls');
    const entries=await f.entries(),admitted=entries.filter(entry=>entry.kind==='glimmer.agent.tool');
    assert.equal(admitted.length,64);assert.equal(new Set(admitted.map(entry=>(entry.data as any).invocationId)).size,64);
    assert.ok(admitted.every(entry=>(entry.data as any).task===1&&(entry.data as any).runId===run.id));
    const outcomes=entries.filter(entry=>entry.kind==='glimmer.agent.tool-ended');assert.equal(outcomes.length,64);
    assert.equal((outcomes[0].data as any).outcome,'threw');
    await f.host.finish(f.c,run.id);
  }finally{await f.close();}
});

test('hard run clock aborts a stream after headers and aborts an in-flight tool; no deadline rearming',async()=>{
  const f=await fixture();let toolStarted=false;
  const server=http.createServer((_request,response)=>{response.writeHead(200,{'Content-Type':'text/event-stream'});response.write('data: {"synthetic":true}\n\n');});
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const run=await f.host.start(f.c);await f.request(1);
    const signal=f.host.signal(f.c.id,run.id);
    const response=await fetch(`http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}`,{signal});
    assert.equal(response.status,200);const bodyFailure=assert.rejects(response.text(),/wall_time|abort/i);
    const tool=f.host.executeTool(f.c.id,'read',async signal=>{toolStarted=true;return new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));});
    const toolFailure=assert.rejects(tool,/wall_time/);await until(()=>toolStarted);
    f.time.advance(AGENT_BUDGET.wallMs);assert.equal(signal.aborted,true);await bodyFailure;await toolFailure;
    await until(async()=>f.aborts.length===1&&(await f.host.status(f.c)).run?.stopReason==='wall_time');
    const state=await f.host.status(f.c);assert.equal(state.run?.deadlineAt,run.deadlineAt);assert.equal(state.run?.toolCalls,1);
    assert.throws(()=>f.host.requestLimit(f.c.id,1),/wall_time/);
    await assert.rejects(f.host.start(f.c),/already admitted/);assert.equal(f.time.size(),0);
    await f.host.finish(f.c,run.id);assert.equal((await f.host.status(f.c)).run?.stopReason,'wall_time');
  }finally{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));await f.close();}
});

test('recovered host reads the durable ledger but cannot rearm, dispatch, or reset it via settings',async()=>{
  const f=await fixture();let recovered:AgentBudgetHost|undefined;
  try{
    const run=await f.host.start(f.c);await f.request(7);await f.host.executeTool(f.c.id,'read',async()=>({ok:true}));
    const before=await f.host.status(f.c);
    recovered=new AgentBudgetHost(ctx,f.options);f.attach(recovered);
    assert.equal((await recovered.status(f.c)).owned,false);assert.deepEqual((await recovered.status(f.c)).run,before.run);
    await assert.rejects(recovered.start(f.c),/persisted run/);
    await assert.rejects(recovered.prepare({conversationId:f.c.id,taskId:8} as HookApi,ctx),/no admitted runtime owner/);
    await assert.rejects(recovered.executeTool(f.c.id,'read',async()=>{throw Error('Must not execute');}),/no admitted runtime owner/);
    await assert.rejects(recovered.save(f.c,before.settings.version,'chat'),/saved work is active/);
    assert.deepEqual((await recovered.status(f.c)).run,before.run);
    f.time.advance(AGENT_BUDGET.wallMs);await until(async()=>(await recovered!.status(f.c)).run?.stopReason==='wall_time');
    await assert.rejects(recovered.start(f.c),/persisted run/);
    await f.host.close();await recovered.finish(f.c,run.id,'aborted');
    const finished=(await recovered.status(f.c)).run;assert.equal(finished?.modelRequests,1);assert.equal(finished?.toolCalls,1);assert.equal(finished?.stopReason,'wall_time');
  }finally{await recovered?.close();await f.close();}
});

test('finished-run timer callbacks cannot abort a successor run; finish joins an admitted abort before releasing ownership',async()=>{
  const f=await fixture();const released=Promise.withResolvers<void>();let callbackStarted=false;
  try{
    const first=await f.host.start(f.c),staleTimer=f.time.callbacks()[0];await f.host.finish(f.c,first.id);
    const second=await f.host.start(f.c),signal=f.host.signal(f.c.id,second.id);
    staleTimer();assert.equal(signal.aborted,false);assert.equal(f.aborts.length,0);
    const abort=f.options.abort;
    f.options.abort=async(...args)=>{callbackStarted=true;await released.promise;await abort(...args);};
    f.time.advance(AGENT_BUDGET.wallMs);await until(()=>callbackStarted);assert.equal(signal.aborted,true);
    let finished=false;const settlement=f.host.finish(f.c,second.id).then(()=>{finished=true;});
    await assert.rejects(f.host.start(f.c),/already admitted/);assert.equal(finished,false);
    released.resolve();await settlement;assert.equal(finished,true);assert.equal(f.aborts.length,1);
    const third=await f.host.start(f.c);assert.equal(f.host.signal(f.c.id,third.id).aborted,false);await f.host.finish(f.c,third.id);
  }finally{released.resolve();await f.close();}
});

test('explicit stop persists aborted before Pi settlement and survives a success-shaped completion',async()=>{
  const f=await fixture();
  try{
    await f.host.stop(f.c);assert.equal((await f.host.status(f.c)).run,null);assert.equal(f.aborts.length,0);
    const run=await f.host.start(f.c);await f.request(1);const signal=f.host.signal(f.c.id,run.id);
    const stop=f.host.stop(f.c);
    assert.throws(()=>f.host.requestLimit(f.c.id,1),/aborted/,'Admission closes synchronously when stop is requested');
    assert.equal(signal.aborted,true,'Signal cancellation must not wait for the metadata transaction');
    await stop;assert.equal(signal.aborted,true);assert.equal((await f.host.status(f.c)).run?.stopReason,'aborted');
    const finished=await f.host.finish(f.c,run.id,'complete');assert.equal(finished.stopReason,'aborted');assert.equal(finished.modelRequests,1);
    await f.host.stop(f.c);assert.deepEqual((await f.host.status(f.c)).run,finished);assert.equal(f.aborts.length,1);
    assert.equal((await f.entries()).filter(entry=>entry.kind==='glimmer.agent.stopped').length,1);
  }finally{await f.close();}
});

test('real Pi tool successors share one admitted run and budget metadata stays outside provider messages',
  {timeout:10000},async()=>{
    const f=await fileKernel(),storage=new MemoryStorage(),models=createModels(),registry=createRegistry();
    let harness:HarnessT|undefined,c:Conversation|undefined,current:AgentBudgetAdmission|undefined;
    const host=new AgentBudgetHost(ctx,{abort:async id=>{await (await harness?.conversation(id as any,ctx))?.abort(ctx);}});
    const base=nomosProvider({baseUrl:f.base+'/v1',requests:new ReadoutRequests(),maxTokens:8192});
    models.setProvider({...base,streamSimple:(model,context,options)=>{
      assert.ok(c&&current);const admission=current;
      return base.streamSimple(model,context,{...options,signal:options?.signal?AbortSignal.any([options.signal,host.signal(c.id)]):host.signal(c.id),
        onPayload:payload=>({...payload as object,max_tokens:admission.outputTokens})});
    }});
    registry.install(host.extension());
    registry.install(defineExtension({name:'synthetic-budget-observer',hooks:[hook(GenerationTask,{
      beforeRequest:async(_request,api)=>{current=host.requestLimit(api.conversationId,api.taskId);return undefined;}
    })]}));
    const fileExtension=fileToolsExtension({async ready(){},execute:(id,name,_args,signal)=>host.executeTool(id,name,async()=>({ok:true,text:'SYNTHETIC_RESULT'}),signal)});
    registry.install(fileExtension);
    try{
      harness=await Harness.open(storage,{models,registry,settings:{retry:{enabled:false},compaction:{enabled:false},stream:{maxRetries:0}}},ctx);host.attach(harness);
      c=await harness.createConversation({ownership:{kind:'ownerless'},agent:{model:{provider:NOMOS_PROVIDER,modelId:NOMOS_MODEL},tools:fileExtension.tools!.filter(tool=>tool.name==='read'),instructions:'Synthetic budget integration.'}},ctx);
      const settings=await host.settings(c);await host.save(c,settings.version,'agent');const run=await host.start(c);
      f.plan([{name:'read',args:{path:'one'}},{name:'read',args:{path:'two'}},null]);
      const submission=await c.submit({type:'input',content:'Exercise two synthetic tools.',whenBusy:'reject'},ctx);await submission.wait(ctx);
      await host.finish(c,run.id);const state=await host.status(c);
      assert.equal(state.run?.modelRequests,3);assert.equal(state.run?.toolCalls,2);assert.equal(state.run?.stopReason,'complete');
      const entries=(await c.entries({order:'ascending'},100,undefined,ctx)).items;
      const requests=entries.filter(entry=>entry.kind==='glimmer.agent.request').map(entry=>entry.data as unknown as AgentBudgetAdmission);
      assert.equal(new Set(requests.map(request=>request.task)).size,3);assert.ok(requests.every(request=>request.runId===run.id));
      assert.deepEqual(f.requests.map(request=>request.max_tokens),[8192,8192,8192]);
      assert.ok(f.requests.every(request=>JSON.stringify(request.messages).includes('Exercise two synthetic tools.')));
      assert.ok(f.requests.every(request=>!JSON.stringify(request.messages).includes(run.id)));
      assert.ok(f.requests.every(request=>!/glimmer.agent|modelRequests|deadlineAt/.test(JSON.stringify(request.messages))));
      assert.ok(entries.filter(entry=>entry.kind.startsWith('glimmer.agent.')).every(entry=>!entry.model));
    }finally{await host.close();await harness?.close(ctx);await f.close();await rm(f.root,{recursive:true,force:true});}
  });
