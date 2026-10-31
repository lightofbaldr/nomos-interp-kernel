/** Operator-only bounded text snapshots. No require/import/eval, subprocesses or writes to source trees. */
import {constants} from 'node:fs';
import {open,readdir,type FileHandle} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {emptyContent,parseSkill,validateContent,renderedProfile,type ProfileContent,type ProfileOrigin} from 'glimmer-agent/profile-model';

const LIMIT={files:256,depth:6,file:262144,total:2097152,previews:8};
const hash=(v:string|Uint8Array)=>createHash('sha256').update(v).digest('hex');
type SourceFile={path:string;sha256:string;bytes:number;text:string;identity:string;disposition:string};
type Snapshot={source:string;digest:string;files:SourceFile[];content:ProfileContent};
export type ImportPreview={id:string;source:string;digest:string;content:ProfileContent;origin:ProfileOrigin;
  files:{path:string;sha256:string;bytes:number;disposition:string}[];extensions:{path:string;text:string}[];
  reference:string;before:string;after:string;warnings:string[];version:number};
const plain=(p:string)=>p!==''&&!p.includes('\0')&&!p.includes('\\')&&!path.isAbsolute(p)&&p.split('/').every(v=>v!==''&&v!=='.'&&v!=='..');
const identity=(s:Awaited<ReturnType<FileHandle['stat']>>)=>[s.dev,s.ino,s.size,s.mtimeMs,s.ctimeMs,s.mode,s.nlink].join(':');
const fdpath=(f:FileHandle)=>`/proc/self/fd/${f.fd}`;
function kind(p:string){
  if(p==='systemprompt.md')return 'instructions';
  if(/^skills\/[^/]+\/SKILL\.md$/.test(p))return 'inline skill';
  if(p==='guardrails.md'||/^tool_descriptions\/[^/]+\.md$/.test(p))return 'advisory text';
  if(/^extensions\/[^/]+\.ts$/.test(p))return 'code preview only';
  return 'omitted (not active)';
}
async function readBounded(f:FileHandle){
  const chunks:Buffer[]=[];let size=0;
  for(;;){const b=Buffer.alloc(32768),n=(await f.read(b)).bytesRead;if(!n)break;size+=n;if(size>LIMIT.file)throw Error('Import file exceeds 256 KiB.');chunks.push(b.subarray(0,n));}
  return Buffer.concat(chunks);
}
/** Every directory component is opened without following links, retaining all ancestor descriptors.
 * Linux proc-fd anchors descendants to the opened directory even if its host pathname is replaced. */
async function directory(absolute:string){
  if(!path.isAbsolute(absolute)||path.normalize(absolute)!==absolute||absolute==='/')throw Error('Import root/path must be a normalized absolute directory.');
  const handles:FileHandle[]=[];
  try{
    handles.push(await open('/',constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW));
    for(const part of absolute.slice(1).split('/'))handles.push(await open(`${fdpath(handles.at(-1)!)}/${part}`,constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW));
    return {handle:handles.at(-1)!,ids:await Promise.all(handles.map(async f=>{const s=await f.stat();return `${s.dev}:${s.ino}`;})),close:async()=>{for(const f of handles.reverse())await f.close();}};
  }catch(e){for(const f of handles.reverse())await f.close();throw e;}
}
async function scan(handle:FileHandle){
  const files:SourceFile[]=[];let total=0,entries=0;
  async function walk(dir:FileHandle,prefix:string,depth:number){
    if(depth>LIMIT.depth)throw Error('Import tree is too deep.');
    for(const name of (await readdir(fdpath(dir))).sort()){
      if(++entries>LIMIT.files)throw Error('Import tree exceeds 256 entries. Select a single harness directory.');
      const rel=prefix+name;if(!plain(rel))throw Error('Invalid import filename.');
      // O_NONBLOCK prevents a malicious FIFO from blocking before fstat refuses it.
      const child=await open(`${fdpath(dir)}/${name}`,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
      try{
        const before=await child.stat();
        if(before.isDirectory())await walk(child,rel+'/',depth+1);
        else{
          if(!before.isFile()||before.nlink!==1||before.size>LIMIT.file)throw Error('Import requires bounded regular, non-linked files.');
          const data=await readBounded(child);total+=data.length;if(total>LIMIT.total)throw Error('Import exceeds 2 MiB.');
          if(identity(before)!==identity(await child.stat()))throw Error('Import file changed while reading.');
          const disposition=kind(rel);
          const text=disposition.startsWith('omitted')?'':new TextDecoder('utf-8',{fatal:true}).decode(data);
          if(text.includes('\0'))throw Error('Imported text contains a NUL.');
          files.push({path:rel,sha256:hash(data),bytes:data.length,text,identity:identity(before),disposition});
        }
      }finally{await child.close();}
    }
  }
  await walk(handle,'',0);return files;
}
export async function snapshotHarness(root:string,relative:string,afterRead?:()=>Promise<void>):Promise<Snapshot>{
  if(relative!==''&&!plain(relative))throw Error('Select a plain relative harness path under the configured root.');
  if(!path.isAbsolute(root)||path.normalize(root)!==root||root==='/')throw Error('Invalid operator import root.');
  const source=relative?path.join(root,relative):root,opened=await directory(source);
  try{
    const files=await scan(opened.handle);await afterRead?.();
    const again=await directory(source);
    try{
      if(JSON.stringify(opened.ids)!==JSON.stringify(again.ids)||JSON.stringify(files)!==JSON.stringify(await scan(again.handle)))throw Error('Import source changed. Preview refused.');
    }finally{await again.close();}
    const prompt=files.find(f=>f.path==='systemprompt.md');if(!prompt)throw Error('Harness needs systemprompt.md.');
    const content=emptyContent(prompt.text);
    for(const f of files){
      if(f.disposition==='inline skill'){parseSkill(f.text);content.skills.push({markdown:f.text,enabled:true});}
      if(f.disposition==='advisory text')content.advisory.push({name:f.path,text:f.text,enabled:true});
    }
    // Preview may exceed the default budget; the user must explicitly opt in before applying it.
    validateContent({...content,allowLarge:true});
    if(files.filter(f=>f.disposition==='code preview only').length>LIMIT.previews)throw Error('At most eight extension previews.');
    const digest=hash(JSON.stringify(files.map(({path,sha256,bytes,disposition})=>({path,sha256,bytes,disposition}))));
    return {source,digest,files,content};
  }finally{await opened.close();}
}
export function importRoots(raw:string|undefined){
  if(!raw)return [];
  const roots:unknown=JSON.parse(raw);
  if(!Array.isArray(roots)||roots.length>8||!roots.every(r=>typeof r==='string'&&path.isAbsolute(r)&&r!=='/'&&path.normalize(r)===r))throw Error('GLIMMER_HARNESS_IMPORT_ROOTS must be a JSON array of up to eight absolute directories.');
  return roots as string[];
}
export function createImporter(roots:string[]){
  const previews=new Map<string,{conversation:string;expires:number;preview:ImportPreview}>();
  function pick(root:unknown,relative:unknown){
    if(!Number.isInteger(root)||typeof relative!=='string'||!roots[root as number])throw Error('Choose an operator-configured import root and relative path.');
    return snapshotHarness(roots[root as number],relative);
  }
  return {
    roots:()=>roots.map((r,id)=>({id,label:r})),
    async preview(conversation:string,version:number,current:ProfileContent,root:unknown,relative:unknown,base?:{root:unknown;path:unknown}){
      const imported=await pick(root,relative),reference=base?await pick(base.root,base.path):null;
      const origin:ProfileOrigin={source:imported.source,digest:imported.digest,files:imported.files.map(({path,sha256,disposition})=>({path,sha256,disposition})),
        delivery:'Harness² on-demand skills → Glimmer Lab inline skills. No scripts or extensions execute.',reference:reference?`Base harness snapshot: ${reference.source}`:'Current profile (not the original base harness)',referenceDigest:reference?.digest??hash(renderedProfile(current)),edited:false};
      const preview:ImportPreview={id:randomUUID(),source:imported.source,digest:imported.digest,content:imported.content,origin,version,
        files:imported.files.map(({path,sha256,bytes,disposition})=>({path,sha256,bytes,disposition})),extensions:imported.files.filter(f=>f.disposition==='code preview only').map(f=>({path:f.path,text:f.text})),
        reference:origin.reference,before:renderedProfile(reference?.content??current),after:renderedProfile(imported.content),
        warnings:[origin.delivery,'Text/profile subset only: not a reproduction of the Harness² executor.','All tools are off. References to files, scripts or tools in the imported text may not work.','Guardrails and tool descriptions are advisory text, not enforced policy or callable tool schemas.']};
      for(const [id,p] of previews)if(p.expires<Date.now())previews.delete(id);
      if(previews.size>=8)throw Error('Too many pending import previews. Wait for an older preview to expire.');
      previews.set(preview.id,{conversation,expires:Date.now()+15*60*1000,preview:structuredClone(preview)});
      return preview;
    },
    take(id:unknown,conversation:string,version:unknown,allowLarge:unknown){
      const stored=typeof id==='string'?previews.get(id):null;
      if(!stored||stored.conversation!==conversation||stored.expires<Date.now()||stored.preview.version!==version||typeof allowLarge!=='boolean')throw Error('Import preview expired or does not match this session/version. Preview again.');
      const content=validateContent({...stored.preview.content,allowLarge});
      return {content,origin:structuredClone(stored.preview.origin)};
    }
  };
}
