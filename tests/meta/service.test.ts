import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {BACKGROUND_CONTEXT as ctx} from 'glimmer-agent/pi';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {createAnnotator} from '../../src/lib/server/meta-annotator.ts';
import {annotationFor,ANNOTATION_KIND} from '../../src/lib/pi/annotation.ts';
import {defaultRules} from '../../src/lib/pi/alerts.ts';
import {fakeKernel,until,pause} from '../live/fixture.ts';
import {loadLabels} from '../../src/lib/server/pi-assets.ts';
const key='fake-key-only',note='The refusal-related token entered the reasoning workspace twice. Check the answer against its sources.';
async function setup(){
  const f=await fakeKernel(),r=await createRuntime(f.base,f.root+'/session.sqlite'),id=await r.create();
  await r.readout(id,{enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[29]},workspace:{lens:f.assets.lenses[0].id,layers:[44],k:4}},f.assets);
  await r.steer(id,{directions:f.assets.directions[0].id,direction:'afraid',layer:24,strength:-.5},f.assets);
  const c=(await r.harness.conversation(Number(id) as any,ctx))!;
  async function generate(text='Question'){await r.submit(id,text);await until(async()=>!(await r.list()).find(c=>c.id===id)?.busy);const entries=(await c.context(ctx)).entries;return (entries.filter(e=>e.kind==='glimmer.readout.turn').at(-1)!.data as any).request;}
  const v=await loadLabels(f.tokenizerPath,f.tokenizerPin),labels=async()=>({labels:Object.fromEntries(v.labels),tokenizer:v.sha256});
  return {f,r,id,c,generate,labels};
}
test('real Pi persistence, same-turn duplicate suppression and no model-context contamination',async()=>{
  const s=await setup();let runtime=s.r,calls=0;
  try{const request=await s.generate(),before=(await s.c.context(ctx)).messages,annotator=createAnnotator(runtime.harness,{key:async()=>key,labels:s.labels,call:async(payload,k)=>{calls++;assert.equal(k,key);assert.equal(JSON.parse(payload).model,'muse-wonder-1.0');await pause(20);return note;}});
    assert.equal((await annotator.available()).available,true);assert.equal(calls,0);
    const results=await Promise.all([annotator.annotate(s.id,request,defaultRules()),annotator.annotate(s.id,request,defaultRules())]);assert.equal(calls,1);assert.ok(results.some(r=>r.status==='complete'));
    const saved=await annotator.annotate(s.id,request,defaultRules());assert.equal(saved.note,note);assert.equal(calls,1);
    assert.equal(saved.payload_sha256,createHash('sha256').update(saved.payload_json).digest('hex'));
    const context=await s.c.context(ctx);assert.deepEqual(context.messages,before);assert.ok(context.entries.filter(e=>e.kind===ANNOTATION_KIND).every(e=>e.model===undefined));
    assert.equal(annotationFor(context.entries,request)?.note,note);
    await runtime.close();runtime=await createRuntime(s.f.base,s.f.root+'/session.sqlite');
    const restored=createAnnotator(runtime.harness,{key:async()=>null,labels:s.labels,call:async()=>{throw Error('must not call');}});
    assert.equal((await restored.annotate(s.id,request,defaultRules())).note,note);
  }finally{await runtime.close();await s.f.close();}
});
test('missing keys and unfinished turns cannot spend, cooldown applies across turns and failures never retry',async()=>{
  const s=await setup();let calls=0,now=100000;
  try{const request=await s.generate();const missing=createAnnotator(s.r.harness,{key:async()=>null,labels:s.labels});assert.equal((await missing.available()).available,false);await assert.rejects(missing.annotate(s.id,request,defaultRules()),/unavailable/);
    assert.equal((await s.c.context(ctx)).entries.some(e=>e.kind===ANNOTATION_KIND),false);
    const a=createAnnotator(s.r.harness,{key:async()=>key,labels:s.labels,now:()=>now,call:async()=>{calls++;throw Error(key);}});
    const failed=await a.annotate(s.id,request,defaultRules());assert.equal(failed.status,'failed');assert.equal(JSON.stringify(failed).includes(key),false);
    await a.annotate(s.id,request,defaultRules());assert.equal(calls,1);
    const second=await s.generate('Second');await assert.rejects(a.annotate(s.id,second,defaultRules()),/cooldown/);assert.equal(calls,1);
    now+=60001;await a.annotate(s.id,second,defaultRules());assert.equal(calls,2);
    await assert.rejects(a.annotate(s.id,'not-a-turn',defaultRules()));assert.equal(calls,2);
  }finally{await s.r.close();await s.f.close();}
});
test('a pending durable claim after restart is never retried automatically',async()=>{
  const s=await setup();let reopened=s.r;
  try{const request=await s.generate();let release!:(x:string)=>void,entered=false;
    const a=createAnnotator(s.r.harness,{key:async()=>key,labels:s.labels,call:async()=>{entered=true;return new Promise(r=>release=r);}});
    const attempt=a.annotate(s.id,request,defaultRules());await until(()=>entered);
    const pending=annotationFor((await s.c.context(ctx)).entries,request);assert.equal(pending?.status,'pending');
    await s.r.close();release(note);await assert.rejects(attempt);reopened=await createRuntime(s.f.base,s.f.root+'/session.sqlite');
    const a2=createAnnotator(reopened.harness,{key:async()=>key,labels:s.labels,call:async()=>{throw Error('must not retry');}});
    assert.equal((await a2.annotate(s.id,request,defaultRules())).status,'pending');
  }finally{await reopened.close();await s.f.close();}
});
test('an actively streaming turn and a malformed rule cannot initiate an annotation',async()=>{
  const s=await setup();let calls=0;
  try{
    const a=createAnnotator(s.r.harness,{key:async()=>key,labels:s.labels,call:async()=>{calls++;return note;}});
    s.f.hold(true);await s.r.submit(s.id,'Still generating');await until(()=>s.f.requests.length===1);
    await assert.rejects(a.annotate(s.id,'chatcmpl-1',defaultRules()));
    await s.r.abort(s.id);assert.equal(calls,0);s.f.hold(false);
    const request=await s.generate();await assert.rejects(a.annotate(s.id,request,{workspace:{enabled:true,prefixes:[42]}} as any));assert.equal(calls,0);
  }finally{await s.r.close();await s.f.close();}
});
