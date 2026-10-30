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
  for(const response of [()=>Response.json({error:key},{status:401}),()=>{throw Error(key);},()=>Response.json({choices:[{message:{content:key},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'word '.repeat(131)},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'The model lied.'},finish_reason:'stop'}]}),()=>Response.json({choices:[{message:{content:'cut off'},finish_reason:'length'}]}),()=>Response.json({choices:[]})]){
    let calls=0;await assert.rejects(requestNote('{}',key,{fetcher:async()=>{calls++;return response();}}),e=>e instanceof MetaError&&!e.message.includes(key));assert.equal(calls,1);
  }
});
test('rejections name a fixed rule and bounded measurements, never rejected prose',async()=>{
  const cases:[string,unknown,number|null,number|null][]=[
    ['shape',{choices:[]},null,null],['finish_reason',{choices:[{message:{content:'unfinished reply'},finish_reason:'length'}]},2,null],
    ['word_limit',{choices:[{message:{content:'word '.repeat(131)},finish_reason:'stop'}]},131,null],
    ['forbidden_phrase',{choices:[{message:{content:'It lied.'},finish_reason:'stop'}]},2,1],
    ['empty_note',{choices:[{message:{content:'  '},finish_reason:'stop'}]},0,null],
    ['tool_calls',{choices:[{message:{content:'text',tool_calls:[]},finish_reason:'stop'}]},1,null],
    ['character_limit',{choices:[{message:{content:'a'.repeat(4001)},finish_reason:'stop'}]},1,null]
  ];
  for(const[rule,data,count,phrase]of cases)await assert.rejects(requestNote('{}',key,{fetcher:async()=>Response.json(data)}),e=>{
    assert.ok(e instanceof MetaError);assert.equal(e.diagnostic?.rule,rule);assert.equal(e.diagnostic?.word_count,count);assert.equal(e.diagnostic?.phrase_id,phrase);assert.equal('text' in e.diagnostic!,false);return true;
  });
  await assert.rejects(requestNote('{}',key,{fetcher:async()=>new Response('not json')}),e=>e instanceof MetaError&&e.diagnostic?.rule==='json');
  const safe=new MetaError('response',{rule:'finish_reason',finish_reason:key,word_count:NaN,phrase_id:999});assert.equal(safe.diagnostic?.finish_reason,'other');assert.equal(safe.diagnostic?.word_count,null);assert.equal(safe.diagnostic?.phrase_id,null);assert.equal(JSON.stringify(safe).includes(key),false);
});
test('the prompt targets 100 words while the hard limit allows 130 without truncation',async()=>{
  assert.match(JSON.parse(makePayload({} as any)).messages[0].content,/at most 100 words/);
  const words='signal '.repeat(130).trim();assert.equal(await requestNote('{}',key,{fetcher:async()=>Response.json({choices:[{message:{content:words},finish_reason:'stop'}]})}),words);
});
test('one abortable timeout bounds even a transport that never answers',async()=>{
  let aborted=false;const start=Date.now();await assert.rejects(requestNote('{}',key,{timeout:20,fetcher:async(_url,init)=>{init?.signal?.addEventListener('abort',()=>aborted=true);return new Promise(()=>{});}}),/timed out/);
  assert.equal(aborted,true);assert.ok(Date.now()-start<1000);
});
test('output is bounded before parsing and a credential in the proposed payload is refused before transport',async()=>{
  await assert.rejects(requestNote('{}',key,{fetcher:async()=>new Response('x'.repeat(130000))}),/response/);
  let calls=0;await assert.rejects(requestNote(JSON.stringify({text:key}),key,{fetcher:async()=>{calls++;return new Response();}}));assert.equal(calls,0);
});
