import test from 'node:test';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {readFile,access,rm} from 'node:fs/promises';
import {MemoryStorage} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import {createFileBroker} from '../../src/lib/server/file-broker.ts';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {profileAction} from '../../src/lib/server/profile-api.ts';
import {agentKernel} from '../agent/fixture.ts';
import {until} from '../live/fixture.ts';
const PREFIX=(process.env.GLIMMER_MOJO_PREFIX??path.join(os.homedir(),'Projects/nomos-interp-kernel/.pixi/envs/default'));

test('operator-off shell refuses readiness, execution and a forged profile before creating a workspace',async()=>{
  const f=await agentKernel(),root=f.root+'/sandbox',broker=createFileBroker(root,PREFIX),r=await createRuntime(f.base,new MemoryStorage(),broker);
  try{
    const id=await r.create(),p=await r.profile(id);
    assert.equal(broker.status().shell?.enabled,false);
    await assert.rejects(broker.ready(['bash']),/disabled by the operator/);
    await assert.rejects(broker.execute(Number(id),'bash',{command:'touch should-not-exist'}),/disabled by the operator/);
    await assert.rejects(r.saveProfile(id,p.version,{...emptyContent(),tools:['bash']}),/disabled by the operator/);
    await assert.rejects(profileAction(r,null as any,{action:'checkShell',conversation:id}),/disabled by the operator/);
    await assert.rejects(profileAction(r,null as any,{action:'checkShell',conversation:id,shell:true}),/Invalid/);
    assert.deepEqual(await r.profile(id),p);assert.equal(f.requests.length,0);await assert.rejects(access(root));
    // A truthy string is not an operator grant.
    const wrong=createFileBroker(root,PREFIX,{shell:'1' as any});
    try{await assert.rejects(wrong.ready(['bash']),/disabled by the operator/);}finally{await wrong.close();}
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('operator-on does not advertise bash until a profile opts in; real Pi shell results retain both readouts',async()=>{
  const f=await agentKernel(),root=f.root+'/sandbox',broker=createFileBroker(root,PREFIX,{shell:true}),r=await createRuntime(f.base,new MemoryStorage(),broker);
  try{
    const id=await r.create(),p=await r.profile(id);
    assert.deepEqual(p.active?.content.tools,[]);assert.equal(broker.status().shell?.available,false);
    const enabled=await r.setAgentMode(id,p.version,true,f.assets);
    assert.equal(enabled.profile.active?.content.tools?.includes('bash'),false);
    const checked=await profileAction(r,null as any,{action:'checkShell',conversation:id});
    assert.equal(checked.tools?.shell?.available,true);
    const saved=await r.saveProfile(id,enabled.profile.version,{...enabled.profile.active!.content,tools:[...enabled.profile.active!.content.tools!,'bash']});
    f.plan([{name:'write',args:{path:'check_mojo.py',content:'from pathlib import Path\nPath("proof.txt").write_text("synthetic-shell-ok")\nprint("<script>not executable markup</script>")\n'}},{name:'bash',args:{command:'python3 check_mojo.py'}},null]);
    await r.submit(id,'Synthetic shell integration.');await until(()=>!r.busy(),25000);
    assert.equal(f.requests.length,3);assert.ok(f.requests.every(q=>q.tools.some((t:any)=>t.function.name==='bash')&&q.nomos.readout.emotion&&q.nomos.readout.workspace));
    const bridge=await r.bridge(id,async()=>{}),snapshot=bridge.snapshot;await bridge.stop();
    const tool=snapshot.pi.entries.filter(e=>e.kind==='pi.tool-result').at(-1)!;
    assert.equal((tool.model?.[0] as any).isError,false);assert.match(JSON.stringify(tool),/not executable markup/);
    const readouts=snapshot.pi.entries.filter(e=>e.kind==='glimmer.readout.turn').map(e=>e.data as any);
    assert.equal(readouts.length,3);assert.ok(readouts.every(t=>t.status==='complete'&&t.records.some((v:any)=>v.source==='generated'&&v.emotion&&v.workspace)));
    assert.ok(snapshot.pi.entries.filter(e=>e.kind==='glimmer.profile.used').every(e=>(e.data as any).profile.digest===saved.active?.digest));
    const budget=await r.budget(id);assert.equal(budget.run?.modelRequests,3);assert.equal(budget.run?.toolCalls,2);
    assert.equal(await readFile(root+`/workspaces/${id}/proof.txt`,'utf8'),'synthetic-shell-ok');
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('shell nonzero command exits are failed tool results, never success inferred from output',async()=>{
  const f=await agentKernel(),broker=createFileBroker(f.root+'/sandbox',undefined,{shell:true});
  try{await broker.ready(['bash']);const r=await broker.execute(1,'bash',{command:'printf "success"; exit 7'});
    assert.equal(r.ok,false);const value=JSON.parse(r.text);assert.equal(value.exit_code,7);assert.equal(value.stdout,'success');
    await assert.rejects(broker.execute(1,'bash',{command:'true',env:{TOKEN:'decoy'}}),/only command/);
  }finally{await broker.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('application abort joins shell teardown before reusing the workspace; detached children cannot write later',async()=>{
  const f=await agentKernel(),root=f.root+'/sandbox',broker=createFileBroker(root,undefined,{shell:true}),controller=new AbortController();
  try{
    await broker.ready(['bash']);
    const work=broker.execute(1,'bash',{command:'(sleep 2; printf late > late.txt) & printf ready > ready.txt; wait'},controller.signal);
    const rejected=assert.rejects(work,/interrupted/);
    await until(async()=>{try{return await readFile(root+'/workspaces/1/ready.txt','utf8')==='ready';}catch{return false;}},10000);
    controller.abort();await rejected;
    assert.equal((await broker.execute(1,'ls',{path:'.'})).ok,true);
    await new Promise(resolve=>setTimeout(resolve,2200));await assert.rejects(access(root+'/workspaces/1/late.txt'));
  }finally{await broker.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
