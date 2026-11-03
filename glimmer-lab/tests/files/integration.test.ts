import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rm,readFile,mkdir,symlink} from 'node:fs/promises';
import {BACKGROUND_CONTEXT as ctx,AgentDoc,MemoryStorage} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {createFileBroker} from '../../src/lib/server/file-broker.ts';
import {fileKernel} from './fixture.ts';
import {until} from '../live/fixture.ts';
import {spawn} from 'node:child_process';
import {once,EventEmitter} from 'node:events';
import {installShutdown} from '../../src/lib/server/shutdown.ts';

test('real Pi + real sandbox: durable calls, scoped files, per-request tools, reload, independent fork and no filesystem rewind',async()=>{
  const f=await fileKernel(),root=f.root+'/workspaces',db=f.root+'/sessions.sqlite';let files=createFileBroker(root),r=await createRuntime(f.base,db,files);
  try{
    const id=await r.create(),s=await r.profile(id);assert.deepEqual(s.active?.content.tools,[]);
    await r.checkTools();assert.equal(r.toolStatus().available,true);
    const saved=await r.saveProfile(id,s.version,{...emptyContent('Use your workspace.'),tools:['read','write','edit','ls','find','grep']});
    f.plan([{name:'write',args:{path:'main.mojo',content:'def main():\n    print("hello")\n'}},{name:'read',args:{path:'main.mojo'}},null]);
    await r.submit(id,'write and read');await until(async()=>!(await r.list()).find(c=>c.id===id)?.busy,15000);
    assert.equal(f.requests.length,3);assert.ok(f.requests.every(b=>b.tools.length===6));
    const c=(await r.harness.conversation(Number(id) as any,ctx))!,entries=(await c.entries({order:'ascending'},100,undefined,ctx)).items;
    const results=entries.filter(e=>e.kind==='pi.tool-result');assert.equal(results.length,2);assert.ok(results.every(e=>(e.model?.[0] as any).isError===false));
    assert.match(JSON.stringify(results),/hello/);
    const used=entries.filter(e=>e.kind==='glimmer.profile.used');assert.equal(used.length,3);assert.ok(used.every(e=>(e.data as any).profile.digest===saved.active?.digest));
    assert.equal(await readFile(root+`/workspaces/${id}/main.mojo`,'utf8'),'def main():\n    print("hello")\n');
    const fork=await r.forkProfile(id,entries.at(-1)!.id);
    assert.deepEqual((await r.profile(fork)).active?.content.tools,saved.active?.content.tools);
    assert.equal((await files.execute(Number(fork),'read',{path:'main.mojo'})).ok,false);
    const active=await r.profile(id),initial=entries.find(e=>e.kind==='glimmer.profile.changed')!;
    await r.restoreProfile(id,active.version,initial.id);assert.deepEqual((await r.profile(id)).active?.content.tools,[]);
    assert.equal((await files.execute(Number(id),'read',{path:'main.mojo'})).ok,true);
    await r.close();files=createFileBroker(root);r=await createRuntime(f.base,db,files);
    assert.deepEqual((await r.profile(id)).active?.content.tools,[]);
    assert.equal((await files.execute(Number(id),'read',{path:'main.mojo'})).ok,true);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('availability failure refuses activation and requests; text-only sessions still run',async()=>{
  const f=await fileKernel();await mkdir(f.root+'/elsewhere');await symlink(f.root+'/elsewhere',f.root+'/linked-root');
  const files=createFileBroker(f.root+'/linked-root'),r=await createRuntime(f.base,new MemoryStorage(),files);
  try{
    const id=await r.create(),s=await r.profile(id);await assert.rejects(r.checkTools(),/root/);
    await assert.rejects(r.saveProfile(id,s.version,{...emptyContent(),tools:['read']}),/root/);
    assert.deepEqual(await r.profile(id),s);assert.equal(f.requests.length,0);assert.equal(r.toolStatus().available,false);
    await r.submit(id,'text only');await until(async()=>!(await r.list())[0].busy);assert.equal(f.requests[0].tools?.length??0,0);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('removing tools during a held tool round waits through later rounds and applies only before a fresh user submission',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox');let release!:()=>void,started=false;
  const held=new Promise<void>(resolve=>release=resolve);
  const r=await createRuntime(f.base,new MemoryStorage(),{...files,execute:async(...args)=>{
    if(args[1]==='write'){started=true;await held;}return files.execute(...args);
  }});
  try{
    const id=await r.create(),s=await r.profile(id),saved=await r.saveProfile(id,s.version,{...emptyContent('tools'),tools:['read','write']});
    f.plan([{name:'write',args:{path:'a',content:'round one'}},{name:'read',args:{path:'a'}},null]);
    await r.submit(id,'run');await until(()=>started);
    const pending=await r.saveProfile(id,saved.version,emptyContent('no tools'));assert.deepEqual(pending.pending?.content.tools,[]);
    assert.deepEqual((await r.harness.snapshot(AgentDoc,Number(id) as any,ctx))?.tools,['read','write']);
    release();await until(async()=>!(await r.list())[0].busy,15000);assert.ok(f.requests.every(b=>b.tools.length===2));
    await r.submit(id,'next');await until(async()=>!(await r.list())[0].busy,10000);assert.equal(f.requests.at(-1).tools?.length??0,0);
    assert.equal((await files.execute(Number(id),'read',{path:'a'})).ok,true);
  }finally{release();await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('broker shutdown cancels queued operations and joins them; no calls after close',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox');
  try{
    await files.ready();const abort=new AbortController();abort.abort();
    await assert.rejects(files.execute(1,'write',{path:'never',content:'x'},abort.signal));
    await assert.rejects(readFile(f.root+'/sandbox/workspaces/1/never'));
    const calls=Array.from({length:4},()=>files.execute(1,'ls',{path:'.'}));
    const settled=Promise.allSettled(calls);await files.close();await settled;
    await assert.rejects(files.execute(1,'ls',{path:'.'}),/unavailable/);
  }finally{await files.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('actual process crash after a file effect: Pi recovery reports interruption and never repeats an unsafe write',async()=>{
  const f=await fileKernel(),db=f.root+'/crash.sqlite',root=f.root+'/sandbox';
  f.plan([{name:'write',args:{path:'once',content:'COMMITTED_BEFORE_CRASH'}},null]);
  const child=spawn(process.execPath,['tests/files/crash-child.ts',f.base,db,root],{stdio:['ignore','pipe','pipe']});
  let log='',err='';child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>err+=b);
  let r:Awaited<ReturnType<typeof createRuntime>>|undefined;
  try{
    await until(()=>{if(child.exitCode!==null)throw Error(err);return log.includes('EFFECT_COMMITTED');},15000);
    const id=log.match(/CONVERSATION (\d+)/)![1];child.kill('SIGKILL');await once(child,'exit');
    let calls=0;const files=createFileBroker(root);
    r=await createRuntime(f.base,db,{...files,execute:async(...args)=>{calls++;return files.execute(...args);}});
    const c=(await r.harness.conversation(Number(id) as any,ctx))!;
    r.harness.resume(); // explicit scheduling, not merely opening a read-only viewer
    await until(async()=>{const e=(await c.entries({order:'ascending'},100,undefined,ctx)).items;return e.some(e=>e.kind==='pi.tool-result');},15000);
    const entries=(await c.entries({order:'ascending'},100,undefined,ctx)).items;
    assert.match(JSON.stringify(entries.filter(e=>e.kind==='pi.tool-result')),/interrupted/);
    assert.equal(calls,0);assert.equal(await readFile(root+`/workspaces/${id}/once`,'utf8'),'COMMITTED_BEFORE_CRASH');
  }finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await once(child,'exit');}await r?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('server signals and adapter shutdown invoke and join runtime cleanup once',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox'),r=await createRuntime(f.base,new MemoryStorage(),files);
  const signals=new EventEmitter();let called=0;
  const lifecycle=installShutdown(async()=>{called++;await r.close();},signals);
  try{
    await r.checkTools();signals.emit('SIGTERM');signals.emit('SIGINT');signals.emit('sveltekit:shutdown');await lifecycle.stop();
    assert.equal(called,1);await assert.rejects(r.create(),/closing/);await assert.rejects(files.execute(1,'ls',{path:'.'}),/unavailable/);
  }finally{lifecycle.dispose();await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
