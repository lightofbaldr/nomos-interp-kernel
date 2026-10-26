import {lstat, readFile, realpath} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

// Private workstation default; override on another host. Never copied into static/build.
export const DEFAULT_STEERING_DATA = '/home/adam/Projects/glimmer-steering-diff-scratch-20261026/public/data';
const safe = (s:unknown):s is string => typeof s==='string' && s.length>0 && s.length<500 &&
  s.split('/').every(p=>p!=='.' && p!=='..' && /^[A-Za-z0-9_.-]+$/.test(p));
const hash = (b:Uint8Array) => createHash('sha256').update(b).digest('hex');

async function localFile(root:string, relative:string, limit:number) {
  if(!safe(relative)) throw new Error('Invalid local data path');
  const base=path.resolve(root);
  if(await realpath(base)!==base) throw new Error('Symlinked data root refused');
  let name=base;
  const parts=relative.split('/');
  for(const [i,part] of parts.entries()){
    name=path.join(name,part);const st=await lstat(name);
    if(st.isSymbolicLink() || (i<parts.length-1 ? !st.isDirectory() : !st.isFile())) throw new Error('Non-regular data path refused');
    if(i===parts.length-1 && st.size>limit) throw new Error('Local data file too large');
  }
  return readFile(name);
}

/** Read-only allowlist, rooted in an operator-owned local catalog. No URL fetch or model call. */
export async function readSteeringFile(root:string, relative:string) {
  if(!safe(relative)) throw new Error('Invalid local data path');
  const catalogBytes=await localFile(root,'catalog.json',2_000_000);
  const catalog=JSON.parse(catalogBytes.toString());
  if(catalog.schema!=='glimmer.local-catalog/1' || !Array.isArray(catalog.datasets) || !catalog.datasets.length || catalog.datasets.length>100) throw new Error('Invalid local catalog');
  const allowed=new Map<string,string>(), ids=new Set<string>();
  for(const d of catalog.datasets){
    if(!safe(d.id) || d.id.includes('/') || ids.has(d.id) || !d.hashes || typeof d.hashes!=='object' || Array.isArray(d.hashes)) throw new Error('Invalid dataset');
    ids.add(d.id);
    for(const [rel,sha] of Object.entries(d.hashes)){
      if(!safe(rel) || !rel.endsWith('.json') || typeof sha!=='string' || !/^[a-f0-9]{64}$/.test(sha)) throw new Error('Invalid catalog entry');
      allowed.set(`${d.id}/${rel}`,sha);
    }
    if(!allowed.has(`${d.id}/index.json`))throw new Error('Missing index binding');
  }
  if(relative==='catalog.json')return catalogBytes;
  const expected=allowed.get(relative);
  if(!expected)throw new Error('File not in catalog');
  const bytes=await localFile(root,relative,64_000_000);
  if(hash(bytes)!==expected)throw new Error('Local bundle changed');
  return bytes;
}
