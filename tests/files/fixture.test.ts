import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {rm} from 'node:fs/promises';
import {fileKernel} from './fixture.ts';
import {until} from '../live/fixture.ts';

test('root probes do not consume the file tool response plan',async()=>{
  const f=await fileKernel();
  try{
    f.plan([{name:'ls',args:{path:'.'}}]);
    for(const method of ['GET','HEAD'])assert.equal((await fetch(f.base,{method})).status,404);
    assert.equal(f.requests.length,0);assert.deepEqual(f.errors,[]);
    const response=await fetch(f.base+'/v1/chat/completions',{method:'POST',body:'{}'});
    assert.equal(response.status,200);assert.match(await response.text(),/"name":"ls"/);assert.equal(f.requests.length,1);
  }finally{await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('file fixture joins a deliberately interrupted request body during teardown',async()=>{
  const f=await fileKernel();let request:ReturnType<typeof http.request>|undefined;
  try{
    request=http.request(f.base+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','Transfer-Encoding':'chunked'}});
    request.on('error',()=>{});request.flushHeaders();await until(()=>f.pending()===1);
    await f.close();await until(()=>f.pending()===0);assert.deepEqual(f.errors,[]);assert.equal(f.requests.length,0);
  }finally{request?.destroy();await rm(f.root,{recursive:true,force:true});}
});
test('file fixture refuses a completed malformed request and retains its failure',async()=>{
  const f=await fileKernel();
  try{
    const response=await fetch(f.base+'/v1/chat/completions',{method:'POST',body:'',headers:{Connection:'close'}});
    assert.equal(response.status,400);assert.equal(f.requests.length,0);
    await assert.rejects(f.close(),/File fixture errors: POST \/v1\/chat\/completions: SyntaxError/);
  }finally{await rm(f.root,{recursive:true,force:true});}
});
