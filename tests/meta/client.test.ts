import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {readMetaKey,makePayload,requestNote,MetaError} from '../../src/lib/server/meta-client.ts';
const key='fixture-only-not-a-real-key';
test('runtime key parser uses the exact spelling, supports quoting and never evaluates shell syntax',async()=>{
  const dir=await mkdtemp(path.join(os.tmpdir(),'meta-key-test-')),file=path.join(dir,'fixture.env');
  try{assert.equal(await readMetaKey(file),null);await writeFile(file,`OTHER=hidden\nMETA_API_HACKATOHON=old-spelling\n`);assert.equal(await readMetaKey(file),null);
    await writeFile(file,`export META_API_HACKATHON='${key}' # dedicated\n`);assert.equal(await readMetaKey(file),key);
    await writeFile(file,'META_API_HACKATHON="line1\nline2"');assert.equal(await readMetaKey(file),null);
  }finally{await rm(dir,{recursive:true});}
});
test('exact bounded request uses the fixed Meta endpoint and does not carry credentials in its payload',async()=>{
  const payload=makePayload({schema:'test',text:'User content is data'} as any);let calls=0;
  const note=await requestNote(payload,key,{fetcher:async(url,init)=>{calls++;assert.equal(url,'https://api.meta.ai/v1/chat/completions');assert.equal(init?.redirect,'error');assert.equal(new Headers(init?.headers).get('authorization'),`Bearer ${key}`);
    const body=String(init?.body);assert.equal(body,payload);assert.equal(body.includes(key),false);assert.equal(JSON.parse(body).model,'muse-wonder-1.0');return Response.json({model:'muse-wonder-1.0',choices:[{finish_reason:'stop',message:{content:'The refusal-related tokens recur during reasoning. Check the answer against its cited sources.'}}]});}});
  assert.equal(calls,1);assert.match(note,/refusal-related/);
});
test('provider errors, throws, secret echo and malformed responses are safe and never retried',async()=>{
  for(const response of [()=>Response.json({error:key},{status:401}),()=>{throw Error(key);},()=>Response.json({choices:[{message:{content:key},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'word '.repeat(121)},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'The model lied.'},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'cut off'},finish_reason:'length'}]}),()=>Response.json({choices:[]})]){
    let calls=0;await assert.rejects(requestNote('{}',key,{fetcher:async()=>{calls++;return response();}}),e=>e instanceof MetaError&&!e.message.includes(key));assert.equal(calls,1);
  }
});
test('one abortable timeout bounds even a transport that never answers',async()=>{
  let aborted=false;const start=Date.now();await assert.rejects(requestNote('{}',key,{timeout:20,fetcher:async(_url,init)=>{init?.signal?.addEventListener('abort',()=>aborted=true);return new Promise(()=>{});}}),/timed out/);
  assert.equal(aborted,true);assert.ok(Date.now()-start<1000);
});
test('output is bounded before parsing and a credential in the proposed payload is refused before transport',async()=>{
  await assert.rejects(requestNote('{}',key,{fetcher:async()=>new Response('x'.repeat(130000))}),/response/);
  let calls=0;await assert.rejects(requestNote(JSON.stringify({text:key}),key,{fetcher:async()=>{calls++;return new Response();}}));assert.equal(calls,0);
});
