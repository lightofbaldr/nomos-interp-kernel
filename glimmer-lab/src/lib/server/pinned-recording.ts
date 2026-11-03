import {open,realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';

export const sha256=(bytes:Uint8Array|string)=>createHash('sha256').update(bytes).digest('hex');

/** Operator-owned, quiescent capture tree. Neither root nor relative path comes
 * from the browser. Refuse symlinks in the entire resolved path; O_NOFOLLOW also
 * protects the final open. This is not a defence against a hostile parent-dir race.
 * Read at most the checked size + 1, not an unbounded readFile after stat.
 */
export async function pinnedBytes(root:string,relative:string,pin:string,cap:number,allowEmpty=false):Promise<Buffer>{
  if(!path.isAbsolute(root)||!relative||path.isAbsolute(relative)||relative.includes('\\')
    ||relative.split('/').some(p=>!p||p==='.'||p==='..')||!/^[a-f0-9]{64}$/.test(pin))throw Error('Invalid pinned path.');
  const file=path.join(root,relative);
  if(await realpath(file)!==file)throw Error('Symlinked capture refused.');
  const fd=await open(file,constants.O_RDONLY|constants.O_NOFOLLOW);
  try{
    const stat=await fd.stat();
    if(!stat.isFile()||stat.size>cap||(!allowEmpty&&stat.size===0))throw Error('Invalid capture size.');
    const bytes=Buffer.alloc(stat.size+1);let n=0;
    while(n<bytes.length){const {bytesRead}=await fd.read(bytes,n,bytes.length-n,null);if(!bytesRead)break;n+=bytesRead;}
    if(n!==stat.size)throw Error('Capture size changed while reading.');
    const result=bytes.subarray(0,n);
    if(sha256(result)!==pin)throw Error('Capture identity changed.');
    return result;
  }finally{await fd.close();}
}
