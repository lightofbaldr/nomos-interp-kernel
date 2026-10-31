import type {Runtime} from './pi-runtime.ts';
import type {createImporter} from './profile-import.ts';
export async function profileAction(runtime:Runtime,imports:ReturnType<typeof createImporter>,body:Record<string,unknown>){
  const keys:Record<string,string[]>={save:['content','version'],restore:['entry','version'],fork:['entry'],preview:['root','path','base'],import:['preview','version','allowLarge'],checkTools:[]};
  const action=body.action;
  if(typeof action!=='string'||!keys[action]||Object.keys(body).some(k=>!['action','conversation',...keys[action]].includes(k)))throw Error('Invalid profile action/field.');
  if(typeof body.conversation!=='string')throw Error('Conversation required.');
  const id=body.conversation;
  if(action==='checkTools'){await runtime.profile(id);return {tools:await runtime.checkTools()};}
  if(action==='save')return {profile:await runtime.saveProfile(id,body.version as number,body.content)};
  if(action==='restore')return {profile:await runtime.restoreProfile(id,body.version as number,body.entry as number)};
  if(action==='fork')return {conversation:await runtime.forkProfile(id,body.entry as number)};
  if(action==='preview'){
    if(body.base!==undefined&&(!body.base||typeof body.base!=='object'||Array.isArray(body.base)||Object.keys(body.base).some(k=>!['root','path'].includes(k))))throw Error('Invalid base reference.');
    const profile=await runtime.profile(id);
    return {preview:await imports.preview(id,profile.version,profile.active!.content,body.root,body.path,body.base as {root:unknown;path:unknown}|undefined)};
  }
  const p=imports.take(body.preview,id,body.version,body.allowLarge);
  return {profile:await runtime.saveProfile(id,body.version as number,p.content,p.origin)};
}
