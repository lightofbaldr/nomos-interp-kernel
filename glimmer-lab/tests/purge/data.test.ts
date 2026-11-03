import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdir,writeFile,readFile,rm,symlink,lstat} from 'node:fs/promises';
import {BACKGROUND_CONTEXT as ctx,LiveDoc,InboxDoc} from 'glimmer-agent/pi';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {runtimeManager} from '../../src/lib/server/pi-manager.ts';
import {previewPurge,executePurge,purgeTarget,purgeWorkspaces} from '../../src/lib/server/conversation-data.ts';
import {fakeKernel,until} from '../live/fixture.ts';

const tables=['conversations','entries','tasks','submissions','documents','document_revisions','record_ids'];
async function fixture(){
  const f=await fakeKernel(),database=f.root+'/sessions.sqlite';
  const manager=runtimeManager(database,()=>createRuntime(f.base,database));
  return {f,database,manager,async close(){await manager.close();await f.close();await rm(f.root,{recursive:true,force:true});}};
}
async function seed(x:Awaited<ReturnType<typeof fixture>>,text:string){
  const id=await x.manager.use(r=>r.create());
  await x.manager.use(async r=>{
    await r.readout(id,{enabled:true,emotion:{directions:x.f.assets.directions[0].id,layers:[29]},workspace:{lens:x.f.assets.lenses[0].id,layers:[44],k:16}},x.f.assets);
    await r.submit(id,text);
  });
  await until(()=>x.manager.use(async r=>!r.busy()));
  await x.manager.use(async r=>{const c=await r.harness.conversation(Number(id) as any,ctx);await c!.commit(tx=>tx.appendEntry(c!.id,{kind:'glimmer.meta.annotation',data:{note:text}}),ctx);});
  return id;
}
async function workspace(database:string,id:string){
  const root=database+'.workspaces';
  for(const dir of [root,root+'/workspaces',root+'/locks',root+'/workspaces/'+id])await mkdir(dir,{recursive:true,mode:0o700});
  await writeFile(root+'/locks/'+id,'',{mode:0o600});await writeFile(root+'/workspaces/'+id+'/source.mojo','owned conversation file');
  return root+'/workspaces/'+id;
}
function snapshot(database:string){const db=new DatabaseSync(database,{readOnly:true});try{return Object.fromEntries(tables.map(t=>[t,db.prepare(`SELECT * FROM ${t}`).all()]));}finally{db.close();}}

test('selected purge deletes persisted messages, readouts, notes, docs, tasks and sandbox files; other data survives reopening',async()=>{
  const x=await fixture();
  try{
    const first=await seed(x,'PURGE_PRIVATE_CANARY_87b4'),keep=await seed(x,'keep this conversation');
    const deletedWork=await workspace(x.database,first),keptWork=await workspace(x.database,keep);
    const external=x.f.root+'/outside.txt';await writeFile(external,'outside retained');await symlink(external,deletedWork+'/link.txt');
    let closed=false;const bridge=await x.manager.use(r=>r.bridge(first,async()=>{}));void bridge.closed.then(()=>closed=true);
    const before=snapshot(x.database),p=await x.manager.preview({scope:'conversation',conversation:first});
    assert.ok(p.entries>5);assert.ok(p.documents>0);assert.deepEqual(p.ids,[first]);
    const result=await x.manager.purge(p.target,p.revision,'DELETE');assert.deepEqual(result.deleted,[first]);assert.equal(result.warning,null);assert.equal(closed,true);
    await assert.rejects(lstat(deletedWork),{code:'ENOENT'});assert.equal(await readFile(keptWork+'/source.mojo','utf8'),'owned conversation file');assert.equal(await readFile(external,'utf8'),'outside retained');
    const after=snapshot(x.database);
    for(const table of tables){assert.doesNotMatch(JSON.stringify(after[table]),/PURGE_PRIVATE_CANARY/);assert.ok(after[table].length<before[table].length,table);}
    for(const table of ['entries','tasks','submissions'])assert.deepEqual(after[table],before[table].filter(r=>String(r.conversation_id)===keep));
    const keptTasks=new Set(before.tasks.filter(r=>String(r.conversation_id)===keep).map(r=>r.id));
    const keptDocs=before.documents.filter(r=>r.scope_kind==='conversation'?String(r.owner_id)===keep:r.scope_kind==='task'&&keptTasks.has(r.owner_id));
    assert.deepEqual(after.documents,keptDocs);
    const docIds=new Set(keptDocs.map(r=>r.id));assert.deepEqual(after.document_revisions,before.document_revisions.filter(r=>docIds.has(r.document_id)));
    const listed=await x.manager.use(r=>r.list());assert.deepEqual(listed.map(c=>c.id),[keep]);
    await assert.rejects(x.manager.use(r=>r.profile(first)),/not found/);
    const fresh=await x.manager.use(r=>r.create());assert.ok(Number(fresh)>Number(keep),'IDs never reused for a stale URL');
    assert.ok(!(await readFile(x.database)).includes(Buffer.from('PURGE_PRIVATE_CANARY_87b4')),'secure_delete plus vacuum removed this logical test string; not a physical erasure claim');
    assert.equal(x.f.requests.length,2,'deletion creates no model call');
  }finally{await x.close();}
});
test('all purge leaves no conversation-owned table records, includes orphan workspaces, and preserves assets/probe and ID allocator',async()=>{
  const x=await fixture();
  try{
    const id=await seed(x,'private all-data canary');await workspace(x.database,id);await workspace(x.database,'999999');
    await mkdir(x.database+'.workspaces/workspaces/probe',{mode:0o700});await writeFile(x.database+'.workspaces/workspaces/probe/marker','keep probe');
    const token=await readFile(x.f.tokenizerPath),p=await x.manager.preview({scope:'all'});
    assert.equal((await x.manager.purge(p.target,p.revision,p.confirmation)).conversations,1);
    for(const values of Object.values(snapshot(x.database)))assert.equal(values.length,0);
    assert.deepEqual(await x.manager.use(r=>r.list()),[]);
    assert.equal(await x.manager.use(r=>r.canAutoCreate()),false);
    await assert.rejects(lstat(x.database+'.workspaces/workspaces/999999'),{code:'ENOENT'});
    assert.deepEqual(await readFile(x.f.tokenizerPath),token);assert.equal(await readFile(x.database+'.workspaces/workspaces/probe/marker','utf8'),'keep probe');
    assert.ok(Number(await x.manager.use(r=>r.create()))>Number(id));
  }finally{await x.close();}
});
test('even an empty purge persists the no-automatic-recreation state and invalidates its old confirmation',async()=>{
  const x=await fixture();
  try{
    assert.equal(await x.manager.use(r=>r.canAutoCreate()),true);
    const p=await x.manager.preview({scope:'all'});assert.equal(p.conversations,0);
    await x.manager.purge(p.target,p.revision,p.confirmation);
    assert.equal(await x.manager.use(r=>r.canAutoCreate()),false);
    await assert.rejects(x.manager.purge(p.target,p.revision,p.confirmation),/changed/);
  }finally{await x.close();}
});
test('a live/inbox document cannot be ignored merely because the task table is idle',async()=>{
  const x=await fixture();
  try{
    const id=await seed(x,'retained state');
    for(const state of ['run','generation','tools','nestedTools','compactions','inbox']){
      await x.manager.use(r=>r.harness.commit(async tx=>{
        if(state==='inbox'){const d=await tx.doc(InboxDoc,Number(id) as any);d.items=[{id:1} as any];}
        else{const d:any=await tx.doc(LiveDoc,Number(id) as any);d[state]=['run','generation'].includes(state)?{}:[{}];}
      },ctx));
      const p=await x.manager.preview({scope:'all'});
      await assert.rejects(x.manager.purge(p.target,p.revision,p.confirmation),/state is not settled/);
      await x.manager.use(r=>r.harness.commit(async tx=>{
        if(state==='inbox'){const d=await tx.doc(InboxDoc,Number(id) as any);d.items=[];}
        else{const d:any=await tx.doc(LiveDoc,Number(id) as any);delete d[state];}
      },ctx));
    }
  }finally{await x.close();}
});
test('stale preview, wrong text, malformed scope and dependent fork refuse without closing or deleting data',async()=>{
  const x=await fixture();
  try{
    const id=await seed(x,'retained'),p=await x.manager.preview({scope:'all'});
    for(const v of [null,{},[],{scope:'all',conversation:id},{scope:'conversation',conversation:'../2'},{scope:'conversation',conversation:2},{scope:'unknown'}])assert.throws(()=>purgeTarget(v));
    await assert.rejects(x.manager.purge(p.target,p.revision,'delete'),/Type PURGE ALL/);
    await x.manager.use(r=>r.create());await assert.rejects(x.manager.purge(p.target,p.revision,p.confirmation),/changed/);
    await x.manager.use(async r=>{const c=await r.harness.conversation(Number(id) as any,ctx);const e=(await c!.entries({order:'ascending'},100,undefined,ctx)).items[0];await r.forkProfile(id,e.id);});
    await assert.rejects(x.manager.preview({scope:'conversation',conversation:id}),/depends/);
    assert.equal((await x.manager.use(r=>r.list())).length,3);assert.match(JSON.stringify(snapshot(x.database)),/retained/);
  }finally{await x.close();}
});
test('active generation, outstanding HTTP/annotation lease and maintenance reject competing mutations',async()=>{
  const x=await fixture();
  try{
    const id=await seed(x,'kept'),p=await x.manager.preview({scope:'all'});
    let release!:()=>void;const held=x.manager.use(async()=>new Promise<void>(r=>release=r));await until(()=>!!release);
    await assert.rejects(x.manager.purge(p.target,p.revision,p.confirmation),/active/);release();await held;
    x.f.hold(true);await x.manager.use(r=>r.submit(id,'held generation'));await until(()=>x.f.requests.length===2);
    await assert.rejects(x.manager.purge(p.target,p.revision,p.confirmation),/generating/);
    await x.manager.use(r=>r.abort(id));await until(()=>x.manager.use(async r=>!r.busy()));
    const ready=await x.manager.preview({scope:'all'}),purging=x.manager.purge(ready.target,ready.revision,ready.confirmation);
    await assert.rejects(x.manager.use(r=>r.create()),/maintenance/);await assert.rejects(x.manager.purge(ready.target,ready.revision,ready.confirmation),/active/);
    await purging;assert.deepEqual(await x.manager.use(r=>r.list()),[]);
  }finally{await x.close();}
});
test('cleanup failure retains database, refuses unknown schema and symlinked database',async()=>{
  const x=await fixture();
  try{
    await seed(x,'retained on failure');const p=await x.manager.preview({scope:'all'});await x.manager.close();const before=snapshot(x.database);
    await assert.rejects(executePurge(x.database,p.target,p.revision,p.confirmation,async()=>{throw Error('cleanup failed');}),/cleanup failed/);assert.deepEqual(snapshot(x.database),before);
    await symlink(x.database,x.f.root+'/alias.sqlite');await assert.rejects(previewPurge(x.f.root+'/alias.sqlite',p.target),/unsafe/);
    const db=new DatabaseSync(x.database);db.exec('CREATE TABLE future_schema (value TEXT)');db.close();
    await assert.rejects(previewPurge(x.database,p.target),/schema/);
    assert.deepEqual(snapshot(x.database),before);
  }finally{await x.close();}
});
test('workspace parent and selected-root symlinks are refused and never traversed',async()=>{
  const x=await fixture();
  try{
    const id=await seed(x,'keep'),work=await workspace(x.database,id),target=x.f.root+'/sentinel';await mkdir(target);await writeFile(target+'/secret','intact');
    await rm(work,{recursive:true});await symlink(target,work);
    await assert.rejects(purgeWorkspaces(x.database,{scope:'all'},[id]),/cleanup/);assert.equal(await readFile(target+'/secret','utf8'),'intact');
    await rm(work);await rm(x.database+'.workspaces/workspaces',{recursive:true});await symlink(target,x.database+'.workspaces/workspaces');
    await assert.rejects(purgeWorkspaces(x.database,{scope:'all'},[id]),/cleanup/);assert.equal(await readFile(target+'/secret','utf8'),'intact');
  }finally{await x.close();}
});
