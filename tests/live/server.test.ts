import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRuntime,validateReadout,validateSteer} from '../../src/lib/server/pi-runtime.ts';
import {sameOrigin,jsonBody} from '../../src/lib/server/origin.ts';
import {bridgeStream} from '../../src/lib/server/bridge-stream.ts';
import {assetCatalog,loadLabels} from '../../src/lib/server/pi-assets.ts';
import {fakeKernel,until,pause} from './fixture.ts';
import {readBridge} from '../../src/lib/pi/stream.ts';
import {reducePi,emptyPi,messagesOf,textOf} from '../../src/lib/pi/model.ts';
import {MemoryStorage} from 'glimmer-agent/pi';

test('same-origin protection includes metadata GETs, hostile origins, and DNS rebinding',()=>{
  for(const method of ['GET','POST'])for(const origin of ['http://evil.test','null','http://localhost:9999'])assert.equal(sameOrigin(new Request('http://127.0.0.1:9999/api/pi',{method,headers:{origin}}),new URL('http://127.0.0.1:9999/api/pi')),false);
  const url=new URL('http://127.0.0.1:9999/api/pi');assert.equal(sameOrigin(new Request(url),url),false);
  assert.equal(sameOrigin(new Request(url,{headers:{'sec-fetch-site':'same-origin',referer:'http://127.0.0.1:9999/'}}),url),true);
  const evil=new URL('http://attacker.test:9999/api/pi');assert.equal(sameOrigin(new Request(evil,{headers:{origin:evil.origin}}),evil),false);
});
test('control JSON is bounded and must be an object',async()=>{
  for(const body of ['null','[]','bad',JSON.stringify({text:'x'.repeat(33000)})])await assert.rejects(jsonBody(new Request('http://localhost/',{method:'POST',headers:{'Content-Type':'application/json'},body})));
});
test('pinned manifests and isolated token labels fail closed',async()=>{
  const f=await fakeKernel();try{
    assert.deepEqual(await assetCatalog(f.registry,{readout:{supported:true,assets:[...f.assets.directions,...f.assets.lenses].map(x=>x.id)}},f.tokenizerPin),f.assets);
    assert.equal((await assetCatalog(f.registry,{},null)).supported,false);
    const v=await loadLabels(f.tokenizerPath,f.tokenizerPin);assert.equal(v.labels.get(501),' cannot');assert.equal(v.labels.get(999),undefined);
    await assert.rejects(loadLabels(f.tokenizerPath,'a'.repeat(64)));
  }finally{await f.close();}
});
test('strict controls accept signed steering and reject absent/foreign metadata',async()=>{
  const f=await fakeKernel();try{
    const c={enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[29]},workspace:null};
    validateReadout(c,f.assets);validateSteer({directions:f.assets.directions[0].id,direction:'afraid',layer:24,strength:-.5},f.assets);
    for(const bad of [{...c,emotion:{...c.emotion,layers:[29,29]}},{...c,workspace:undefined},{...c,enabled:false},{...c,emotion:{...c.emotion,extra:1}}])assert.throws(()=>validateReadout(bad as any,f.assets));
    for(const strength of [NaN,Infinity,-2.1,true])assert.throws(()=>validateSteer({directions:f.assets.directions[0].id,direction:'afraid',layer:24,strength} as any,f.assets));
  }finally{await f.close();}
});
test('real Pi harness: durable sessions, readouts, versioned steering, reconnect, exclusive admission and contract failures',async()=>{
  const f=await fakeKernel();let runtime=await createRuntime(f.base,f.root+'/sessions.sqlite');
  try{
    const id=await runtime.create(),second=await runtime.create();assert.notEqual(id,second);
    await runtime.readout(id,{enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[20,24,29]},workspace:{lens:f.assets.lenses[0].id,layers:[44],k:4}},f.assets);
    await runtime.steer(id,{directions:f.assets.directions[0].id,direction:'afraid',layer:24,strength:-.5},f.assets);
    let state=emptyPi();const bridge=await runtime.bridge(id,async m=>{state=reducePi(state,m);});state=reducePi(state,{type:'bridge.snapshot',snapshot:bridge.snapshot});
    await runtime.submit(id,'Hello');await assert.rejects(runtime.submit(second,'concurrent'),/already/);
    await until(()=>state.turns[0]?.status==='complete'&&!state.busy);
    assert.equal(state.turns[0].records.length,43);assert.equal(state.turns[0].summary?.counts.records,43);
    assert.equal(messagesOf(state).filter(e=>e.message.role==='assistant').map(e=>textOf(e.message)).join(''),'Hello from Pi.');
    assert.match(messagesOf(state).map(e=>textOf(e.message,'thinking')).join(''),/carefully/);
    assert.equal(f.requests[0].nomos.steer.strength,-.5);assert.equal(state.settings?.version,2);
    await bridge.stop();await runtime.close();runtime=await createRuntime(f.base,f.root+'/sessions.sqlite');
    const restored=await runtime.bridge(id,async()=>{});const again=reducePi(emptyPi(),{type:'bridge.snapshot',snapshot:restored.snapshot});
    assert.equal(again.turns[0].records.length,43);assert.equal(again.settings?.steer?.strength,-.5);assert.equal((await runtime.list()).length,2);
    await restored.stop();
    f.fault('summary');let failed=emptyPi();const b=await runtime.bridge(id,async m=>{failed=reducePi(failed,m);});failed=reducePi(failed,{type:'bridge.snapshot',snapshot:b.snapshot});
    await runtime.submit(id,'Missing summary');await until(()=>failed.turns.at(-1)?.status==='contract_error');assert.ok(failed.turns.at(-1)?.error);await b.stop();
  }finally{await runtime.close();await f.close();}
});
test('browser detach does not cancel the live conversation; explicit stop does',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());try{
    const id=await r.create();f.hold(true);const b=await r.bridge(id,async()=>{});
    await r.submit(id,'hold');await until(()=>f.requests.length===1);await b.stop();assert.equal((await r.list())[0].busy,true);
    await r.abort(id);await until(async()=>!(await r.list())[0].busy);
  }finally{await r.close();await f.close();}
});
test('transport sends snapshot first even when events race; a large snapshot does not deadlock; cancel detaches',async()=>{
  let stopped=0;const abort=new AbortController();const snapshot={pi:{payload:'x'.repeat(1_200_000)},settings:{version:0}} as any;
  const response=bridgeStream(async send=>{await send({type:'readout.error',conversationId:1,request:null,error:'raced'} as any);return {snapshot,stop:async()=>{stopped++;}};},abort.signal);
  const frames:any[]=[];const consume=readBridge(response,w=>{frames.push(w);if(frames.length===2)abort.abort();});
  await assert.rejects(Promise.race([consume,pause(3000).then(()=>{throw Error('stream deadlock');})]),/Bridge disconnected/);
  assert.deepEqual(frames.map(f=>f.type),['bridge.snapshot','readout.error']);await until(()=>stopped===1);
});
