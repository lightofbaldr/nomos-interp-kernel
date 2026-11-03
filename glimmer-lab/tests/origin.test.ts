import {test} from 'node:test';
import assert from 'node:assert/strict';
import {canonicalLoopbackPage,sameOrigin} from '../src/lib/server/origin.ts';

const canonical='http://127.0.0.1:5241';
function page(host='localhost:5241',path='/workspace?turn=2',method='GET',accept='text/html'){
  const url=new URL(path,canonical);
  return {url,request:new Request(url,{method,headers:{host,accept}})};
}
function destination(v:ReturnType<typeof page>,origin:string|undefined=canonical){
  return canonicalLoopbackPage(v.request,v.url,origin);
}

test('loopback page aliases redirect to the configured origin, preserving path and query',()=>{
  assert.equal(destination(page()),canonical+'/workspace?turn=2');
  assert.equal(destination(page('[::1]:5241')),canonical+'/workspace?turn=2');
  assert.equal(destination(page('localhost:5241','/','HEAD')),canonical+'/');
});
test('canonical pages and servers without a fixed origin do not redirect',()=>{
  assert.equal(destination(page('127.0.0.1:5241')),null);
  const {request,url}=page();
  assert.equal(canonicalLoopbackPage(request,url,undefined),null);
  assert.equal(destination(page(),''),null);
});
test('API calls, non-page assets and mutations never gain an alias exception',()=>{
  for(const path of ['/api','/api/pi','/api/nomos/health'])assert.equal(destination(page('localhost:5241',path)),null);
  for(const method of ['POST','PUT','DELETE','OPTIONS'])assert.equal(destination(page('localhost:5241','/',method)),null);
  assert.equal(destination(page('localhost:5241','/_app/start.js','GET','*/*')),null);
});
test('foreign hosts, ports and malformed authorities cannot redirect',()=>{
  for(const host of ['evil.test:5241','localhost.evil.test:5241','localhost:9999','localhost:5241@evil.test',
    'localhost:5241/extra','localhost:5241?x=1','localhost:5241#x',''])assert.equal(destination(page(host)),null,host);
  for(const origin of ['http://evil.test:5241','http://127.0.0.1:5241/path','http://user@127.0.0.1:5241',
    'http://127.0.0.1:5241?x=1','broken'])assert.equal(destination(page(),origin),null,origin);
});
test('a network-path-looking pathname stays on the configured origin',()=>{
  const v=page();v.url.pathname='//evil.test/path';
  const result=destination(v);assert.ok(result);assert.equal(new URL(result).origin,canonical);
});
test('loopback aliases are still different origins for the API',()=>{
  const url=new URL('/api/pi',canonical);
  for(const origin of ['http://localhost:5241','http://evil.test','null']){
    assert.equal(sameOrigin(new Request(url,{method:'POST',headers:{origin}}),url),false);
  }
  assert.equal(sameOrigin(new Request(url,{headers:{'sec-fetch-site':'same-origin',referer:'http://localhost:5241/'}}),url),false);
  assert.equal(sameOrigin(new Request(url,{headers:{'sec-fetch-site':'same-origin',referer:canonical+'/'}}),url),true);
  assert.equal(sameOrigin(new Request(url),url),false);
});
