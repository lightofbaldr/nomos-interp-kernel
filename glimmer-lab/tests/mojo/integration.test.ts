import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {test} from 'node:test';
import {readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {MemoryStorage,BACKGROUND_CONTEXT as ctx} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import {createFileBroker,FILE_BROKER_PATH} from '../../src/lib/server/file-broker.ts';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {fileKernel} from '../files/fixture.ts';
import {until} from '../live/fixture.ts';
const PREFIX=(process.env.GLIMMER_MOJO_PREFIX??path.join(os.homedir(),'Projects/nomos-interp-kernel/.pixi/envs/default'));

test('real Pi writes then invokes Mojo; durable output and profile bind the actual fixed capability',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox',PREFIX),r=await createRuntime(f.base,new MemoryStorage(),files);
  try{
    const id=await r.create(),s=await r.profile(id);assert.deepEqual(s.active?.content.tools,[]);
    await r.checkTools(['mojo']);assert.equal(r.toolStatus().mojo?.available,true);
    const saved=await r.saveProfile(id,s.version,{...emptyContent('Build a simple source.'),tools:['write','mojo']});
    f.plan([{name:'write',args:{path:'main.mojo',content:'def main():\n    print("pi-mojo-ok")\n'}},{name:'mojo',args:{path:'main.mojo'}},null]);
    await r.submit(id,'Create and run it.');await until(async()=>!(await r.list())[0].busy,20000);
    assert.equal(f.requests.length,3);assert.ok(f.requests.every(v=>v.tools.map((t:any)=>t.function.name).join(',')==='write,mojo'));
    const c=(await r.harness.conversation(Number(id) as any,ctx))!,entries=(await c.entries({order:'ascending'},100,undefined,ctx)).items;
    const result=entries.filter(e=>e.kind==='pi.tool-result').at(-1)!;
    assert.equal((result.model?.[0] as any).isError,false);assert.match(JSON.stringify(result),/pi-mojo-ok/);
    const used=entries.filter(e=>e.kind==='glimmer.profile.used');assert.ok(used.every(e=>(e.data as any).profile.digest===saved.active?.digest));
    assert.equal(await readFile(f.root+`/sandbox/workspaces/${id}/main.mojo`,'utf8'),'def main():\n    print("pi-mojo-ok")\n');
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('missing compiler refuses Mojo without changing a profile or disabling file tools',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox'),r=await createRuntime(f.base,new MemoryStorage(),files);
  try{
    const id=await r.create(),s=await r.profile(id);
    await assert.rejects(r.saveProfile(id,s.version,{...emptyContent(),tools:['mojo']}),/GLIMMER_MOJO_PREFIX/);
    assert.deepEqual(await r.profile(id),s);assert.equal(f.requests.length,0);
    await r.saveProfile(id,s.version,{...emptyContent(),tools:['read']});assert.equal(r.toolStatus().available,true);
    assert.equal(r.toolStatus().mojo?.available,false);
  }finally{await r.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('broker paths remain bound to the app when cwd contains a substitute runtime',async()=>{
  const f=await fileKernel(),cwd=process.cwd();let files:ReturnType<typeof createFileBroker>|undefined;
  try{
    await mkdir(f.root+'/runtime/files',{recursive:true});
    await writeFile(f.root+'/runtime/files/broker.py','raise RuntimeError("SUBSTITUTE RAN")\n');
    process.chdir(f.root);files=createFileBroker(f.root+'/sandbox');
    assert.ok(FILE_BROKER_PATH.startsWith(cwd+'/runtime/'));await files.ready();
    assert.equal((await files.execute(1,'ls',{path:'.'})).ok,true);
  }finally{process.chdir(cwd);await files?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('aborting compiled execution joins the sandbox; no late result or automatic replay',async()=>{
  const f=await fileKernel(),files=createFileBroker(f.root+'/sandbox',PREFIX),controller=new AbortController();
  try{
    await files.execute(1,'write',{path:'main.mojo',content:'def main():\n    while True:\n        pass\n'});
    const call=files.execute(1,'mojo',{path:'main.mojo'},controller.signal);
    const rejection=assert.rejects(call,/interrupted/);
    await new Promise(resolve=>setTimeout(resolve,1800));controller.abort();await rejection;
    assert.equal((await files.execute(1,'read',{path:'main.mojo'})).ok,true);
  }finally{await files.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
