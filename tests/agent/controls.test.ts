import test from 'node:test';
import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {MemoryStorage} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {fakeKernel,until} from '../live/fixture.ts';
import {AGENT_TOOLS,agentEnabled,agentContent,agentReadout} from '../../src/lib/agent/model.ts';
import {emptyPi,lastRow,selectedRow} from '../../src/lib/pi/model.ts';
import {initialChannel} from '../../src/lib/workspace/model.ts';
import {agentKernel} from './fixture.ts';
import {effectiveReplyBudget} from '../../src/lib/pi/generation.ts';

function broker(){
  let fail=false,held:Promise<void>|null=null;const checks:unknown[]=[];
  return {checks,fail:(v:boolean)=>{fail=v;},hold:(v:Promise<void>|null)=>{held=v;},
    async ready(tools:unknown){checks.push(tools);if(held)await held;if(fail)throw Error('Isolation probe failed.');},
    status:()=>({available:!fail,reason:'fixture',policy:'glimmer.files/bwrap-1',mojo:{available:!fail,reason:'fixture'}}),
    async execute(){return {ok:true,text:'fixture'};},async close(){}};
}
test('one activation checks tools, preserves the harness and steering, and enables both readouts',async()=>{
  const f=await fakeKernel(),b=broker(),r=await createRuntime(f.base,new MemoryStorage(),b);
  try{
    const id=await r.create(),s=await r.profile(id);assert.equal(agentEnabled(s.active?.content),false);
    const custom=await r.saveProfile(id,s.version,{...emptyContent('Keep my instructions.'),skills:[{markdown:'---\nname: verify\ndescription: Verify source\n---\nKeep this skill.',enabled:true}]});
    await r.steer(id,{directions:f.assets.directions[0].id,direction:'afraid',layer:24,strength:.5},f.assets);
    const result=await r.setAgentMode(id,custom.version,true,f.assets);
    assert.equal(agentEnabled(result.profile.active?.content),true);assert.deepEqual(result.profile.active?.content.tools,AGENT_TOOLS);
    assert.equal(result.profile.active?.content.instructions,'Keep my instructions.');assert.deepEqual(result.profile.active?.content.skills,custom.active?.content.skills);
    assert.ok(b.checks.some(t=>JSON.stringify(t)===JSON.stringify(AGENT_TOOLS)));
    assert.deepEqual(result.settings.readout.workspace,{lens:f.assets.lenses[0].id,layers:[44],k:16});
    assert.deepEqual(result.settings.readout.emotion,{directions:f.assets.directions[0].id,layers:[29]});
    assert.equal(result.settings.steer?.strength,.5);assert.equal(f.requests.length,0);
    assert.equal(result.budget.settings.mode,'agent');
    const off=await r.setAgentMode(id,result.profile.version,false);
    assert.deepEqual(off.profile.active?.content.tools,[]);assert.deepEqual(off.settings.readout,result.settings.readout);
    assert.equal(off.budget.settings.mode,'chat');
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('real Pi request admission binds the whole tool loop and stops before request 33',async()=>{
  const f=await agentKernel(),b=broker(),r=await createRuntime(f.base,new MemoryStorage(),b);
  try{
    const id=await r.create(),s=await r.profile(id);await r.setAgentMode(id,s.version,true,f.assets);
    f.plan(Array.from({length:33},()=>({name:'ls',args:{path:'.'}})));
    await r.submit(id,'Synthetic call-cap control.');await until(()=>!r.busy(),15000);
    assert.equal(f.requests.length,32);assert.ok(f.requests.every(q=>q.max_tokens===8192));
    const {run}=await r.budget(id);assert.equal(run?.status,'finished');assert.equal(run?.stopReason,'model_requests');assert.equal(run?.modelRequests,32);assert.equal(run?.toolCalls,32);
    const s2=await r.budget(id);await r.setBudget(id,s2.settings.version,'chat');f.plan([null]);await r.submit(id,'Synthetic normal chat.');await until(()=>!r.busy());assert.equal(f.requests.at(-1)?.max_tokens,6144);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('failed checks, stale versions and absent/ambiguous assets do not enable tools',async()=>{
  const f=await fakeKernel(),b=broker(),r=await createRuntime(f.base,new MemoryStorage(),b);
  try{
    const id=await r.create(),initial=await r.profile(id),settings=await r.host.settings(Number(id) as any);
    b.fail(true);await assert.rejects(r.setAgentMode(id,initial.version,true,f.assets),/Isolation/);
    assert.deepEqual(await r.profile(id),initial);assert.deepEqual(await r.host.settings(Number(id) as any),settings);
    b.fail(false);await assert.rejects(r.setAgentMode(id,999,true,f.assets),/changed/);
    await assert.rejects(r.setAgentMode(id,initial.version,true,{...f.assets,lenses:[]}),/lens/);
    await assert.rejects(r.setAgentMode(id,initial.version,true,{...f.assets,lenses:[...f.assets.lenses,{...f.assets.lenses[0],id:'second'}]}),/lens/);
    assert.deepEqual(await r.profile(id),initial);assert.equal(f.requests.length,0);
    await assert.rejects(r.setAgentMode(id,initial.version,'true' as any,f.assets),/Invalid/);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('configuration admission blocks submissions and profile writers until probes settle',async()=>{
  const f=await fakeKernel(),b=broker(),r=await createRuntime(f.base,new MemoryStorage(),b);let release!:()=>void;
  try{
    const id=await r.create(),initial=await r.profile(id);b.hold(new Promise(resolve=>release=resolve));
    const pending=r.setAgentMode(id,initial.version,true,f.assets);await until(()=>b.checks.length>0);
    assert.equal(r.busy(),true);
    await assert.rejects(r.submit(id,'do not send'),/configur/);await assert.rejects(r.saveProfile(id,initial.version,emptyContent()),/configur/);
    await assert.rejects(r.setAgentMode(id,initial.version,false),/settle/);assert.equal(f.requests.length,0);
    release();await pending;assert.equal(r.busy(),false);
  }finally{release?.();await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('inspection of an existing conversation is read-only and rejects unknown IDs',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());
  try{const id=await r.create();await r.assertConversation(id);await assert.rejects(r.assertConversation('../2'),/Invalid/);await assert.rejects(r.assertConversation('999'),/not found/);assert.equal((await r.list()).length,1);assert.equal(f.requests.length,0);}
  finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('Agent mode never enables a shell; current supported settings are preserved without registry order assumptions',()=>{
  const content=agentContent(emptyContent(),true);assert.deepEqual(content.tools,AGENT_TOOLS);assert.equal(content.tools?.includes('bash' as any),false);
  assert.throws(()=>agentContent(content,1 as any),/boolean/);
  const a={supported:true,error:null,tokenizer:null,directions:[{id:'random',names:['random1'],layers:[29]},{id:'emotion',names:['afraid','angry','desperate'],layers:[20,29]}],lenses:[{id:'lens',layers:[36,44]},{id:'other',layers:[44]}]};
  const saved={enabled:true,emotion:{directions:'emotion',layers:[20,29]},workspace:{lens:'lens',layers:[36,44],k:8}};
  assert.deepEqual(agentReadout(a,saved),saved);
});
test('latest measurement remains attributed across tool followups; explicit selection does not borrow',()=>{
  const state=emptyPi(),record={pos:7,token:4,source:'generated',channel:'tool',emotion:null,workspace:{top:[[[4,1]]]},missing:[]} as any;
  state.turns=[{request:'tool-1',turn:0,task:1,header:null,records:[record],status:'complete',error:null,summary:null},{request:'tool-2',turn:1,task:2,header:null,records:[],status:'streaming',error:null,summary:null}];
  assert.equal(lastRow(state)?.turn.request,'tool-1');assert.equal(selectedRow(state,{request:'tool-2',pos:7}),null);
  assert.equal(initialChannel([record]),'tool');assert.equal(initialChannel([{...record,channel:'reasoning'}]),'reasoning');assert.equal(initialChannel([]),'all');
});
test('context-shortened reply ceilings are derived only from a complete matching request; absent evidence is unknown',()=>{
  const used={request:'r',advertisedContextTokens:32768,parameters:{max_tokens:8192}} as any;
  const turn={request:'r',status:'complete',header:{settings:{}},summary:{ranges:{generated:[30000,30100]}}} as any;
  assert.deepEqual(effectiveReplyBudget(used,turn),{requested:8192,prompt:30000,capacity:32768,effective:2767,clamped:true,basis:'saved prompt boundary + advertised capacity; not a returned serve limit'});
  for(const changed of [{...turn,status:'contract_error'},{...turn,request:'other'},{...turn,summary:null},{...turn,header:{settings:null}}])assert.equal(effectiveReplyBudget(used,changed),null);
  assert.equal(effectiveReplyBudget({...used,advertisedContextTokens:undefined},turn),null);
  assert.equal(effectiveReplyBudget(used,{...turn,summary:{ranges:{generated:[100,200]}}})?.clamped,false);
});
