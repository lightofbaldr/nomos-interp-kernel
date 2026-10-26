import assert from 'node:assert/strict';
import {consumeChat,SYSTEM_MESSAGE} from '../src/lib/live.ts';
import {harness} from './harness.mjs';
import {fakeKernel,thought,answer,frame} from './fake-kernel.mjs';
const fixture=await fakeKernel(),h=await harness(false,{NOMOS_API_BASE_URL:fixture.url});let checks=0;
const check=(value,label)=>{assert.ok(value,label);checks++;};
const messages=[{role:'system',content:SYSTEM_MESSAGE},{role:'user',content:'France?'}];
const post=(body,origin=h.base)=>fetch(h.base+'/api/nomos/chat',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
try{
  let r=await fetch(h.base+'/api/nomos/health'),data=await r.json();check(data.health.ready&&data.health.model==='nomos-muse-fixture','Actual upstream health is normalized');check(!JSON.stringify(data).includes('not-for-the-browser'),'Filesystem paths are not exposed');
  r=await fetch(h.base+'/api/nomos/kvstore');data=await r.json();check(data.stats.ram_bytes===1048576&&data.stats.reused_tokens===100,'All metrics use measured fixture values');
  const before=fixture.state.requests.length;
  for(const [body,origin] of [[{messages},'http://other.test'],[{messages,upstream:'http://other.test'},h.base],[{messages:[{role:'user',content:'no fixed system'}]},h.base],[{messages:[messages[0],{role:'user',content:'x'.repeat(65000)}]},h.base]]){r=await post(body,origin);check(r.status>=400,'Known-bad origin/body is refused');}
  check(fixture.state.requests.length===before,'Invalid input never reaches the upstream');
  r=await post({messages});let fields={content:'',reasoning_content:''};const finish=await consumeChat(r,(k,v)=>fields[k]+=v);
  check(fields.content===answer&&fields.reasoning_content===thought&&finish==='stop','Fragmented UTF-8/CRLF stream preserves both exact fields');
  check(fixture.state.requests[0].temperature===0&&fixture.state.requests[0].enable_thinking===true&&fixture.state.requests[0].stream===true,'Greedy reasoning-on stream contract is enforced');
  check(!('model' in fixture.state.requests[0])&&!('emotion' in fixture.state.requests[0]),'No fabricated model or steering fields are forwarded');
  r=await post({messages:[messages[0],{role:'user',content:'[fail]'}]});check(r.status===502,'Upstream failure cannot become a successful empty chat');
  for(const wire of [frame({content:'partial'}),frame({},'stop'), 'data: not-json\n\n',frame({content:5})+'data: [DONE]\n\n']){
    await assert.rejects(()=>consumeChat(new Response(wire,{headers:{'Content-Type':'text/event-stream'}}),()=>{}));checks++;
  }
  fixture.state.failStats=true;r=await fetch(h.base+'/api/nomos/kvstore');data=await r.json();check(data.stats===null&&Boolean(data.error),'Failed counters stay unknown, not zero');
  fixture.state.ready=false;r=await fetch(h.base+'/api/nomos/health');check((await r.json()).health.ready===false,'Not-ready is not rewritten as connected');
  console.log(`MUSE TRANSPORT PASSED: ${checks} controls, isolated CPU fixture only.`);
}finally{await h.close();await fixture.close();}
