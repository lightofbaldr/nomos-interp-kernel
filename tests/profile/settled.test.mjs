import assert from 'node:assert/strict';
import {test} from 'node:test';
import {profileSettled,waitForProfileSettled} from '../../scripts/profile-settled.mjs';
const snapshot=(state={})=>({type:'bridge.snapshot',snapshot:{settings:{conversationId:2},pi:{type:'snapshot',inbox:[],tools:[],nestedTools:[],compactions:[],...state}}});
const response=value=>new Response(`data: ${JSON.stringify(value)}\n\n`);

test('server run, inbox and other in-flight work hold the wait even when Send is visible',async()=>{
  const states=[{run:{inputs:[1]}},{inbox:[{id:1}]},{generation:{attempt:1}},{tools:[{}]},{nestedTools:[{}]},{compactions:[{}]},{}];
  let reads=0;
  await waitForProfileSettled('http://127.0.0.1:12345','2',{intervalMs:0,fetcher:async(url,options)=>{
    assert.equal(url,'http://127.0.0.1:12345/api/pi?conversation=2');assert.equal(options.headers.Origin,'http://127.0.0.1:12345');
    assert.equal(options.redirect,'error');assert.ok(reads<states.length);return response(snapshot(states[reads++]));
  }});
  assert.equal(reads,7);
});

test('missing or foreign state never means settled',()=>{
  assert.equal(profileSettled(snapshot(),'2'),true);
  for(const name of ['inbox','tools','nestedTools','compactions']){
    const value=snapshot();delete value.snapshot.pi[name];assert.throws(()=>profileSettled(value,'2'),/Missing/);
  }
  assert.throws(()=>profileSettled(snapshot(),'3'),/Wrong conversation/);
  assert.throws(()=>profileSettled({type:'pi',event:{type:'run_end'}},'2'),/Expected a server snapshot/);
});

test('a busy server times out instead of falling back to client readiness',async t=>{
  let reads=0,clock=0;t.mock.method(Date,'now',()=>clock);
  await assert.rejects(waitForProfileSettled('http://127.0.0.1:12345','2',{timeoutMs:30,intervalMs:1,
    fetcher:async()=>{reads++;clock=31;return response(snapshot({run:{inputs:[1]}}));}}),/did not settle/);
  assert.equal(reads,1);
});

test('HTTP failure, an ended stream and malformed JSON refuse',async()=>{
  for(const make of [()=>new Response('unavailable',{status:503}),()=>new Response(''),()=>new Response('data: broken\n\n')]){
    await assert.rejects(waitForProfileSettled('http://127.0.0.1:12345','2',{fetcher:async()=>make()}));
  }
});
