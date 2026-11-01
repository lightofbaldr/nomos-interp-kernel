import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {MemoryStorage,BACKGROUND_CONTEXT as ctx} from 'glimmer-agent/pi';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {fakeKernel,until} from '../live/fixture.ts';
const settled=(r:any)=>until(async()=>!r.busy());
async function entries(r:any,id:string){const c=await r.harness.conversation(Number(id),ctx);return (await c.entries({order:'ascending'},500,undefined,ctx)).items;}

test('retry keeps capped reasoning, never adds a user question, and omits the old attempt from subsequent context',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());
  try{
    const id=await r.create();await r.submit(id,'earlier question');await settled(r);
    f.reply({thinking:'CAPPED_PRIVATE_REASONING',answer:'',finish:'length'});
    await r.submit(id,'question to retry');await settled(r);
    const before=await entries(r,id),old=before.findLast((e:any)=>e.kind==='pi.assistant');
    assert.equal(old.model[0].stopReason,'length');f.reply({thinking:'new reasoning',answer:'new answer',finish:'stop'});
    await r.retry(id,old.id);await settled(r);
    assert.deepEqual(f.requests[2].messages,f.requests[1].messages,'exact provider history, no appended question');
    const after=await entries(r,id);assert.deepEqual(after.find((e:any)=>e.id===old.id),old);
    assert.equal(after.filter((e:any)=>e.kind==='pi.user').length,2);
    const marker=after.find((e:any)=>e.kind==='glimmer.retry');assert.equal(marker.data.target,old.id);assert.ok(!marker.model);
    assert.deepEqual(marker.edits,[{target:old.id,action:'omit'}]);
    await r.submit(id,'next');await settled(r);assert.doesNotMatch(JSON.stringify(f.requests[3].messages),/CAPPED_PRIVATE_REASONING|glimmer.retry/);
    assert.equal(f.requests[3].messages.filter((m:any)=>m.role==='user'&&m.content==='question to retry').length,1);
    await assert.rejects(r.retry(id,old.id),/last|retried|eligible/i);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('retry after restart can use explicitly changed sampling; duplicate admission has only one winner',async()=>{
  const f=await fakeKernel();let r=await createRuntime(f.base,f.root+'/retry.sqlite');
  try{
    const id=await r.create();f.reply({answer:'',finish:'length'});await r.submit(id,'single question');await settled(r);
    const old=(await entries(r,id)).findLast((e:any)=>e.kind==='pi.assistant');
    await r.close();r=await createRuntime(f.base,f.root+'/retry.sqlite');const s=await r.generation(id);await r.setGeneration(id,s.version,true);
    f.hold(true);const results=await Promise.allSettled([r.retry(id,old.id),r.retry(id,old.id)]);
    assert.equal(results.filter(x=>x.status==='fulfilled').length,1);await until(()=>f.requests.length===2);
    assert.equal(f.requests[1].temperature,1);assert.deepEqual(f.requests[1].messages,f.requests[0].messages);
    await r.abort(id);await settled(r);
    const after=await entries(r,id);assert.equal(after.filter((e:any)=>e.kind==='glimmer.retry').length,1);
    assert.equal(after.filter((e:any)=>e.kind==='pi.user').length,1);
    await assert.rejects(r.retry(id,old.id),/last|retried|eligible/i);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('answered length stops, ordinary answers, stale targets and busy attempts refuse without a model call',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());
  try{
    const id=await r.create();
    for(const finish of ['stop','length']){
      f.reply({answer:'real answer',finish});await r.submit(id,finish);await settled(r);
      const last=(await entries(r,id)).findLast((e:any)=>e.kind==='pi.assistant'),n=f.requests.length;
      await assert.rejects(r.retry(id,last.id),/capped|reasoning|eligible/i);assert.equal(f.requests.length,n);
    }
    await assert.rejects(r.retry(id,0),/entry|target/i);
    f.reply({answer:'',finish:'length'});await r.submit(id,'capped');await settled(r);
    const old=(await entries(r,id)).findLast((e:any)=>e.kind==='pi.assistant');
    f.hold(true);await r.submit(id,'later');await until(()=>f.requests.length===4);
    await assert.rejects(r.retry(id,old.id),/generat|busy|wait/i);await r.abort(id);await settled(r);
    const n=f.requests.length;await assert.rejects(r.retry(id,old.id),/last|eligible/i);assert.equal(f.requests.length,n);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
