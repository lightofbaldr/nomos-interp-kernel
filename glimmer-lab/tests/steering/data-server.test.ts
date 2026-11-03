import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rm, symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {readSteeringFile} from '../../src/lib/server/steering-data.ts';
const sha=(s:string)=>createHash('sha256').update(s).digest('hex');
async function fixture(){
  const dir=await mkdtemp(path.join(tmpdir(),'glimmer-data-test-'));
  const root=path.join(dir,'data');await mkdir(path.join(root,'one'),{recursive:true});
  const text='{"test":"known bytes"}',catalog={schema:'glimmer.local-catalog/1',datasets:[{id:'one',hashes:{'index.json':sha(text)}}]};
  await writeFile(path.join(root,'one/index.json'),text);await writeFile(path.join(root,'catalog.json'),JSON.stringify(catalog));
  return {root,dir,text,catalog,close:()=>rm(dir,{recursive:true,force:true})};
}
test('adapter returns original bytes, never a re-derived document',async()=>{
  const f=await fixture();try{assert.equal((await readSteeringFile(f.root,'one/index.json')).toString(),f.text);assert.deepEqual(JSON.parse((await readSteeringFile(f.root,'catalog.json')).toString()),f.catalog);}finally{await f.close();}
});
test('unknown files and traversal never become arbitrary filesystem reads',async()=>{
  const f=await fixture();try{await writeFile(path.join(f.root,'secret.json'),'secret');for(const p of ['secret.json','../secret.json','/secret.json','one/../secret.json','one//index.json','one\\index.json','one/%2e%2e/secret.json','one'])await assert.rejects(()=>readSteeringFile(f.root,p));}finally{await f.close();}
});
test('modified payload is refused even with a valid JSON shape',async()=>{
  const f=await fixture();try{await writeFile(path.join(f.root,'one/index.json'),'{}');await assert.rejects(()=>readSteeringFile(f.root,'one/index.json'),/changed/);}finally{await f.close();}
});
test('file, directory and root symlinks are refused',async()=>{
  const f=await fixture();try{
    await writeFile(path.join(f.dir,'elsewhere'),f.text);await rm(path.join(f.root,'one/index.json'));await symlink(path.join(f.dir,'elsewhere'),path.join(f.root,'one/index.json'));
    await assert.rejects(()=>readSteeringFile(f.root,'one/index.json'),/Non-regular/);
    await rm(path.join(f.root,'one'),{recursive:true});await mkdir(path.join(f.dir,'outside'));await writeFile(path.join(f.dir,'outside/index.json'),f.text);await symlink(path.join(f.dir,'outside'),path.join(f.root,'one'));
    await assert.rejects(()=>readSteeringFile(f.root,'one/index.json'),/Non-regular/);
    await symlink(f.root,path.join(f.dir,'alias'));await assert.rejects(()=>readSteeringFile(path.join(f.dir,'alias'),'catalog.json'),/Symlinked/);
  }finally{await f.close();}
});
test('malformed catalog, duplicate ids, bad hashes and missing index refuse',async()=>{
  const f=await fixture();try{
    for(const mutate of [(c:any)=>c.schema='wrong',(c:any)=>c.datasets.push(c.datasets[0]),(c:any)=>c.datasets[0].hashes['index.json']='not-a-hash',(c:any)=>c.datasets[0].hashes={},(c:any)=>c.datasets[0].id='../elsewhere']){
      const c=structuredClone(f.catalog);mutate(c);await writeFile(path.join(f.root,'catalog.json'),JSON.stringify(c));await assert.rejects(()=>readSteeringFile(f.root,'catalog.json'));
    }
  }finally{await f.close();}
});
