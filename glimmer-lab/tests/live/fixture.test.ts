import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {rm} from 'node:fs/promises';
import {fakeKernel,until} from './fixture.ts';

test('GET and HEAD root probes are refused without a synthetic model request',async()=>{
  const f=await fakeKernel();
  try{
    for(const method of ['GET','HEAD'])assert.equal((await fetch(f.base,{method})).status,404);
    assert.equal(f.requests.length,0);assert.deepEqual(f.errors,[]);
  }finally{await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('unsolicited request evidence keeps only bounded caller metadata',async()=>{
  const f=await fakeKernel();
  try{
    const response=await fetch(f.base,{headers:{'User-Agent':'fixture-caller-'+'x'.repeat(600),
      Referer:'http://127.0.0.1/fixture',Origin:'http://127.0.0.1',Authorization:'decoy-secret',Cookie:'decoy-cookie'}});
    assert.equal(response.status,404);assert.equal(f.unsolicitedRequests.length,1);
    const evidence=f.unsolicitedRequests[0];assert.equal(evidence.userAgent?.length,512);
    assert.equal(evidence.referer,'http://127.0.0.1/fixture');assert.equal(evidence.origin,'http://127.0.0.1');
    assert.equal(evidence.remoteAddress,'127.0.0.1');assert.equal(evidence.localPort,Number(new URL(f.base).port));
    assert.doesNotMatch(JSON.stringify(evidence),/decoy-secret|decoy-cookie/);assert.equal(f.requests.length,0);
  }finally{await f.close();await rm(f.root,{recursive:true,force:true});}
});

test('fake kernel joins a deliberately interrupted request body during teardown',async()=>{
  const f=await fakeKernel();let request:ReturnType<typeof http.request>|undefined;
  try{
    request=http.request(f.base+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json','Transfer-Encoding':'chunked'}});
    request.on('error',()=>{});request.flushHeaders();await until(()=>f.pending()===1);
    await f.close();assert.equal(f.pending(),0);assert.deepEqual(f.errors,[]);assert.equal(f.requests.length,0);
  }finally{request?.destroy();await rm(f.root,{recursive:true,force:true});}
});

test('fake kernel retains a completed malformed request as a test failure',async()=>{
  const f=await fakeKernel();
  try{
    const response=await fetch(f.base+'/v1/chat/completions',{method:'POST',body:'',headers:{Connection:'close'}});
    assert.equal(response.status,400);assert.equal(f.requests.length,0);
    await assert.rejects(f.close(),/Kernel fixture errors: POST \/v1\/chat\/completions bytes=0 complete=true: SyntaxError/);
  }finally{await rm(f.root,{recursive:true,force:true});}
});
