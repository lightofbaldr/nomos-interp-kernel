import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rm,mkdir,writeFile} from 'node:fs/promises';
import {MemoryStorage,BACKGROUND_CONTEXT as ctx,AgentDoc} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {profileAction} from '../../src/lib/server/profile-api.ts';
import {createImporter} from '../../src/lib/server/profile-import.ts';
import {fakeKernel,until} from '../live/fixture.ts';

test('app profiles: no live model, version conflict, pending run, exact native instructions and historical attribution',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());try{
    const id=await r.create(),initial=await r.profile(id);assert.deepEqual(initial.active?.content.skills,[]);
    const content={...emptyContent('PROFILE_ALPHA'),skills:[{markdown:'---\nname: safe\ndescription: Work carefully\n---\nSKILL_BETA',enabled:true}]};
    const saved=await r.saveProfile(id,initial.version,content);
    f.hold(true);await r.submit(id,'hold');await until(()=>f.requests.length===1);
    const pending=await r.saveProfile(id,saved.version,emptyContent('PROFILE_NEXT'));
    assert.equal(pending.active?.digest,saved.active?.digest);assert.equal(pending.pending?.content.instructions,'PROFILE_NEXT');
    await assert.rejects(r.saveProfile(id,saved.version,emptyContent('stale')),/Reload/);
    await assert.rejects(profileAction(r,createImporter([]),{action:'save',conversation:id,version:pending.version,content,tools:['bash']}),/field/);
    const c=await r.harness.conversation(Number(id) as any,ctx);assert.ok(c);
    assert.equal((await r.harness.snapshot(AgentDoc,c.id,ctx))?.instructions,'PROFILE_ALPHA');
    assert.match(JSON.stringify(f.requests[0]),/SKILL_BETA/);assert.equal(f.requests[0].tools?.length??0,0);
    await r.abort(id);await until(async()=>!(await r.list()).find(c=>c.id===id)?.busy);
    f.hold(false);await r.submit(id,'next');await until(async()=>!(await r.list()).find(c=>c.id===id)?.busy);
    assert.match(JSON.stringify(f.requests[1]),/PROFILE_NEXT/);
    assert.doesNotMatch(JSON.stringify(f.requests[1]),/SKILL_BETA/,'disabled/removed skill text is not offered again');
    const context=await c.context(ctx);const uses=context.entries.filter(e=>e.kind==='glimmer.profile.used');assert.equal(uses.length,2);
    assert.deepEqual(uses.map(e=>(e.data as any).profile.content.instructions),['PROFILE_ALPHA','PROFILE_NEXT']);
    assert.ok(uses.every(e=>!e.model));assert.doesNotMatch(JSON.stringify(context.messages),/glimmer\.profile|text-only-1/);
    const old=(await c.entries({order:'ascending'},100,undefined,ctx)).items.find(e=>e.kind==='glimmer.profile.changed'&&(e.data as any).active.content.instructions==='PROFILE_ALPHA')!;
    const active=await r.profile(id),restored=await r.restoreProfile(id,active.version,old.id);
    assert.equal(restored.active?.content.instructions,'PROFILE_ALPHA');assert.ok(restored.active!.revision>active.active!.revision);
    const fork=await r.forkProfile(id,old.id);assert.equal((await r.profile(fork)).active?.content.instructions,'PROFILE_ALPHA');assert.equal((await r.profile(fork)).pending,null);
    await assert.rejects(r.restoreProfile(fork,1,999999));
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('SQLite reopen retains profiles; legacy instructions are not replaced; disabled skills are not sent',async()=>{
  const f=await fakeKernel();let r=await createRuntime(f.base,f.root+'/profiles.sqlite');try{
    const id=await r.create(),s=await r.profile(id);
    const saved=await r.saveProfile(id,s.version,{...emptyContent('RESTARTED'),skills:[{markdown:'---\nname: unused\ndescription: Disabled\n---\nNOT_IN_CONTEXT',enabled:false}]});
    await r.close();r=await createRuntime(f.base,f.root+'/profiles.sqlite');assert.deepEqual(await r.profile(id),saved);
    await r.submit(id,'one');await until(async()=>!(await r.list())[0].busy);
    assert.match(JSON.stringify(f.requests[0]),/RESTARTED/);assert.doesNotMatch(JSON.stringify(f.requests[0]),/NOT_IN_CONTEXT/);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
test('operator import applies only the sealed preview, binds provenance and keeps it across a later edit',async()=>{
  const f=await fakeKernel(),r=await createRuntime(f.base,new MemoryStorage());try{
    await mkdir(f.root+'/h');await writeFile(f.root+'/h/systemprompt.md','SEALED_TEXT');
    const imports=createImporter([f.root]),id=await r.create(),s=await r.profile(id);
    const p=await profileAction(r,imports,{action:'preview',conversation:id,root:0,path:'h'});
    assert.ok('preview' in p);const preview=p.preview!;
    await writeFile(f.root+'/h/systemprompt.md','LATER_DISK_TEXT');
    await assert.rejects(profileAction(r,imports,{action:'import',conversation:id,preview:preview.id,version:s.version,allowLarge:false,content:emptyContent('FORGED')}),/field/);
    await profileAction(r,imports,{action:'import',conversation:id,preview:preview.id,version:s.version,allowLarge:false});
    const imported=await r.profile(id);assert.equal(imported.active?.content.instructions,'SEALED_TEXT');assert.equal(imported.active?.origin?.digest,preview.digest);
    const edited=await r.saveProfile(id,imported.version,emptyContent('EDITED'));
    assert.equal(edited.active?.origin?.edited,true);assert.deepEqual(edited.active?.origin?.files,preview.origin.files);
    assert.equal(f.requests.length,0);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
