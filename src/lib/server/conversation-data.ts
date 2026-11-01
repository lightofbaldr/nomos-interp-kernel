/** Pi 1.3.0 has no delete API. This adapter runs only with the harness closed.
 * Keep the explicit schema guard and the persisted/reopen controls on upgrades. */
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {lstat,realpath} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {APP_ROOT} from './app-root.ts';

export type PurgeTarget={scope:'all'}|{scope:'conversation';conversation:string};
export type PurgePlan={target:PurgeTarget;revision:string;ids:string[];conversations:number;entries:number;documents:number;confirmation:string};
export class PurgeConflict extends Error {}
const columns:Record<string,string[]>={
  durable_schema:['singleton','version'],durable_metadata:['singleton','next_id','next_seq'],record_ids:['id','record_type'],
  conversations:['id','owner_conversation_id','owner_task_id','record'],entries:['id','conversation_id','head','commit_seq','record'],
  tasks:['id','conversation_id','kind','status','abort_requested','background','record'],submissions:['id','conversation_id','request_id','status','record'],
  documents:['id','kind','family','key_value','scope_kind','owner_id','created_at','retired_at','record'],document_revisions:['document_id','seq','kind','version','content']
};
export function purgeTarget(value:unknown):PurgeTarget{
  if(!value||typeof value!=='object'||Array.isArray(value))throw new PurgeConflict('Invalid deletion scope.');
  const v=value as Record<string,unknown>;
  if(v.scope==='all'&&Object.keys(v).length===1)return {scope:'all'};
  if(v.scope==='conversation'&&Object.keys(v).length===2&&typeof v.conversation==='string'&&/^[1-9]\d{0,14}$/.test(v.conversation))return {scope:'conversation',conversation:v.conversation};
  throw new PurgeConflict('Invalid deletion scope.');
}
async function open(database:string,readOnly:boolean){
  const file=path.resolve(database),s=await lstat(file);
  if(file!==database||await realpath(file)!==file||!s.isFile()||s.nlink!==1)throw new PurgeConflict('Conversation database path is unsafe. Nothing was deleted.');
  return new DatabaseSync(file,{readOnly,timeout:5000});
}
function schema(db:DatabaseSync){
  const tables=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r=>r.name);
  if(JSON.stringify(tables)!==JSON.stringify(Object.keys(columns).sort())||db.prepare("SELECT name FROM sqlite_master WHERE type IN ('trigger','view')").all().length)throw new PurgeConflict('Unsupported conversation database schema. Nothing was deleted.');
  for(const [name,want] of Object.entries(columns))if(JSON.stringify(db.prepare(`PRAGMA table_info(${name})`).all().map(r=>r.name))!==JSON.stringify(want))throw new PurgeConflict('Unsupported conversation database columns. Nothing was deleted.');
  if(db.prepare('SELECT version FROM durable_schema WHERE singleton=1').get()?.version!==1)throw new PurgeConflict('Unsupported Pi schema version. Nothing was deleted.');
}
const count=(db:DatabaseSync,table:string,where='1')=>Number(db.prepare(`SELECT count(*) AS n FROM ${table} WHERE ${where}`).get()!.n);
function selection(db:DatabaseSync,target:PurgeTarget){
  const rows=db.prepare('SELECT id,record FROM conversations ORDER BY id').all();
  const ids=rows.filter(r=>target.scope==='all'||String(r.id)===target.conversation).map(r=>String(r.id));
  if(target.scope==='conversation'&&!ids.length)throw new PurgeConflict('Conversation no longer exists. Refresh the list.');
  if(!ids.every(id=>/^[1-9]\d{0,14}$/.test(id)))throw new PurgeConflict('Unsupported conversation identity.');
  for(const row of rows){const r=JSON.parse(String(row.record));
    if(!ids.includes(String(row.id))&&[r.parent?.conversationId,r.owner?.conversationId].some(v=>ids.includes(String(v))))throw new PurgeConflict('Another conversation depends on this history. Delete its forks first, or choose Purge all.');
  }
  const conversations=target.scope==='all'?'1':`id IN (${ids.join(',')})`,owned=target.scope==='all'?'1':`conversation_id IN (${ids.join(',')})`;
  const documents=target.scope==='all'?'1':`(scope_kind='conversation' AND owner_id IN (${ids.join(',')})) OR (scope_kind='task' AND owner_id IN (SELECT id FROM tasks WHERE ${owned}))`;
  if(target.scope!=='all'&&count(db,'documents',"scope_kind='session'"))throw new PurgeConflict('This database has shared session documents. Use Purge all to remove them safely.');
  return {ids,conversations,owned,documents};
}
function plan(db:DatabaseSync,target:PurgeTarget):PurgePlan{
  schema(db);
  if(count(db,'tasks',"status<>'terminal'")||count(db,'submissions',"status IN ('queued','placed')"))throw new PurgeConflict('Saved work is still active. Wait for it to finish or stop it before deleting data.');
  const s=selection(db,target),metadata=db.prepare('SELECT * FROM durable_metadata').all();
  const counts={conversations:s.ids.length,entries:count(db,'entries',s.owned),documents:count(db,'documents',s.documents)};
  return {target,revision:createHash('sha256').update(JSON.stringify([target,s.ids,metadata,counts])).digest('hex'),ids:s.ids,...counts,confirmation:target.scope==='all'?'PURGE ALL':'DELETE'};
}
export async function previewPurge(database:string,value:unknown){
  const target=purgeTarget(value),db=await open(database,true);
  try{db.exec('BEGIN');return plan(db,target);}finally{db.close();}
}
/** The monotonic allocator distinguishes an unused installation from one whose
 * conversations were purged, without storing deleted IDs or adding a schema.
 * This prevents even a different browser from recreating a chat on reload. */
export async function unusedDatabase(database:string){
  const db=await open(database,true);
  try{return db.prepare('SELECT next_id FROM durable_metadata WHERE singleton=1').get()?.next_id==='2'&&count(db,'conversations')===0;}finally{db.close();}
}

/** Fixed helper; no caller supplies a filesystem target. It locks every workspace
 * before removing any, refuses symlinked parents, and never follows a child link. */
export async function purgeWorkspaces(database:string,target:PurgeTarget,ids:string[]){
  const env:NodeJS.ProcessEnv={PATH:'/usr/bin:/bin',LANG:'C.UTF-8'};
  for(const k of ['XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'])if(process.env[k])env[k]=process.env[k];
  return new Promise<void>((resolve,reject)=>{
    const child=spawn('/usr/bin/python3',['-I','-B',path.join(APP_ROOT,'runtime/files/purge.py'),database+'.workspaces'],{env,stdio:['pipe','pipe','pipe']});
    let output='',overflow=false;const timer=setTimeout(()=>child.kill('SIGKILL'),60000);
    child.stdin.on('error',()=>{});child.stdout.on('data',b=>{output+=b;if(output.length>8192){overflow=true;child.kill('SIGKILL');}});child.stderr.on('data',()=>{});
    child.on('error',()=>{});child.on('close',code=>{clearTimeout(timer);if(code===0&&!overflow&&output.trim()==='WORKSPACES PURGED')resolve();else reject(new PurgeConflict('Workspace cleanup stopped. Some files may already be removed; conversation records were retained. Resolve busy or unsafe workspace paths before trying again.'));});
    child.stdin.end(JSON.stringify({all:target.scope==='all',ids}));
  });
}
export async function executePurge(database:string,value:unknown,revision:unknown,confirmation:unknown,cleanup=purgeWorkspaces){
  const target=purgeTarget(value),db=await open(database,false);let committed=false,cleaned=false;
  try{
    db.exec('PRAGMA secure_delete=ON; BEGIN IMMEDIATE');
    const p=plan(db,target);
    if(typeof revision!=='string'||revision!==p.revision)throw new PurgeConflict('Conversations changed since confirmation opened. Review the deletion again.');
    if(confirmation!==p.confirmation)throw new PurgeConflict(`Type ${p.confirmation} to confirm.`);
    const s=selection(db,target);
    // No transaction writes before filesystem cleanup succeeds. A cleanup failure
    // leaves records intact, although already removed workspace files cannot be restored.
    await cleanup(database,target,p.ids);
    cleaned=true;
    db.exec(`CREATE TEMP TABLE purge_ids (id INTEGER PRIMARY KEY);
      INSERT INTO purge_ids SELECT id FROM conversations WHERE ${s.conversations};
      INSERT INTO purge_ids SELECT id FROM entries WHERE ${s.owned};
      INSERT INTO purge_ids SELECT id FROM tasks WHERE ${s.owned};
      INSERT INTO purge_ids SELECT id FROM submissions WHERE ${s.owned};
      INSERT INTO purge_ids SELECT id FROM documents WHERE ${s.documents};
      DELETE FROM document_revisions WHERE document_id IN (SELECT id FROM documents WHERE ${s.documents});
      DELETE FROM documents WHERE ${s.documents};
      DELETE FROM entries WHERE ${s.owned}; DELETE FROM submissions WHERE ${s.owned}; DELETE FROM tasks WHERE ${s.owned};
      DELETE FROM conversations WHERE ${s.conversations}; DELETE FROM record_ids WHERE id IN (SELECT id FROM purge_ids);
      UPDATE durable_metadata SET next_seq=next_seq+1, next_id=CASE WHEN next_id='2' THEN '3' ELSE next_id END;
      DROP TABLE purge_ids; COMMIT;`);
    committed=true;
    let warning:string|null=null;
    try{db.exec('VACUUM');const checkpoint=db.prepare('PRAGMA wal_checkpoint(TRUNCATE)').get();if(checkpoint?.busy!==0)throw Error('checkpoint busy');}
    catch{warning='Conversation data was deleted, but database space reclamation could not finish. This is not a secure-erasure guarantee.';}
    return {deleted:p.ids,conversations:p.conversations,warning};
  }catch(e){
    if(cleaned&&!committed)throw new PurgeConflict('Workspace files were removed, but conversation-record deletion failed. Records were retained; review the failure before retrying.');
    throw e;
  }finally{if(!committed)try{db.exec('ROLLBACK');}catch{/* already rolled back */}db.close();}
}
