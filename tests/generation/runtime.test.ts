import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {MemoryStorage,BACKGROUND_CONTEXT as ctx} from 'glimmer-agent/pi';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {fakeKernel,until} from '../live/fixture.ts';

const params=(r:any)=>({temperature:r.temperature,top_p:r.top_p,top_k:r.top_k,max_tokens:r.max_tokens});
const greedy={temperature:0,top_p:1,top_k:0,max_tokens:6144};
const sampled={temperature:1,top_p:0.95,top_k:64,max_tokens:6144};
const settled=(r:any)=>until(async()=>!r.busy());
test('Pi sends explicit greedy parameters and the full reply budget without readouts',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());
  try{const id=await r.create();await r.submit(id,'one');await settled(r);assert.deepEqual(params(f.requests[0]),greedy);}
  finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('sampling is per conversation, versioned, durable and historical attribution never follows a later toggle',async()=>{
  const f=await fakeKernel();let r=await createRuntime(f.base,f.root+'/generation.sqlite');
  try{
    const a=await r.create(),b=await r.create();const initial=await r.generation(a);assert.equal(initial.sampling,false);
    await r.setGeneration(a,initial.version,true);
    await assert.rejects(r.setGeneration(a,initial.version,false),/changed|Reload/);
    await assert.rejects(r.setGeneration(a,initial.version+1,'true' as any),/boolean/);
    await r.submit(a,'sampled');await settled(r);await r.submit(b,'greedy');await settled(r);
    assert.deepEqual(params(f.requests[0]),sampled);assert.deepEqual(params(f.requests[1]),greedy);
    const c=await r.harness.conversation(Number(a) as any,ctx);assert.ok(c);
    const entries=(await c.entries({order:'ascending'},100,undefined,ctx)).items;
    const used=entries.filter(e=>e.kind==='glimmer.generation.used');assert.equal(used.length,1);
    assert.deepEqual(params((used[0].data as any).parameters),sampled);assert.equal((used[0].data as any).mode,'sampled');
    assert.ok(!used[0].model);assert.doesNotMatch(JSON.stringify(f.requests[0].messages),/glimmer.generation|top_k/);
    await r.close();r=await createRuntime(f.base,f.root+'/generation.sqlite');assert.equal((await r.generation(a)).sampling,true);
    const s=await r.generation(a);await r.setGeneration(a,s.version,false);await r.submit(a,'now greedy');await settled(r);
    assert.deepEqual(params(f.requests[2]),greedy);assert.equal((used[0].data as any).mode,'sampled');
    assert.equal((await r.generation(b)).sampling,false);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('held turns cannot change their decoding; captured turns bind the same request to their decoding record',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());
  try{
    const id=await r.create();await r.readout(id,{enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[29]},workspace:null},f.assets);
    f.hold(true);await r.submit(id,'hold');await until(()=>f.requests.length===1);
    const s=await r.generation(id);await assert.rejects(r.setGeneration(id,s.version,true),/settle|generat|busy/i);
    await r.abort(id);await settled(r);f.hold(false);
    await r.submit(id,'done');await settled(r);
    const c=await r.harness.conversation(Number(id) as any,ctx);const e=(await c!.entries({order:'ascending'},200,undefined,ctx)).items;
    const turn=e.findLast(e=>e.kind==='glimmer.readout.turn')!.data as any;
    const used=e.findLast(e=>e.kind==='glimmer.generation.used')!.data as any;
    assert.equal(used.request,turn.request);assert.equal(used.task,turn.task);assert.deepEqual(used.parameters,greedy);
    assert.deepEqual(params(f.requests[1]),greedy);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
