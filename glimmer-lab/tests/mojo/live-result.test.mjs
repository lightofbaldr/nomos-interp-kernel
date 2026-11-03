import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createHash} from 'node:crypto';
import {outcome} from '../../scripts/mojo-live-result.mjs';
const source='def main():\n    print("ok")\n',profile={active:{digest:'test-profile'}};
function fixture(finalNewline=true){
  const bytes=Buffer.from(finalNewline?source:source.slice(0,-1)),hash=createHash('sha256').update(bytes).digest('hex');
  const checks=Object.fromEntries(['root_ro','runtime_ro','work_ro','build_rw','compiler_ro','config_ro','caps_zero','no_new_privs','home_empty','run_empty','cwd','env','fsize'].map(k=>[k,true]));
  const phase={exit_code:0,reason:null,metadata:{source_sha256:hash},evidence:{isolation:{checks},resources:{MemoryMax:'4294967296',MemorySwapMax:'0'}},stdout:''};
  const mojo={compile:structuredClone(phase),run:{...structuredClone(phase),stdout:'ok\n'}};
  const messages=[{role:'assistant',content:[{type:'toolCall',name:'write',id:'w',arguments:{path:'main.mojo',content:bytes.toString()}}]},
    {role:'toolResult',toolCallId:'w',isError:false,content:[{type:'text',text:'{"written":true}'}]},
    {role:'assistant',content:[{type:'toolCall',name:'mojo',id:'m',arguments:{path:'main.mojo'}}]},
    {role:'toolResult',toolCallId:'m',isError:false,content:[{type:'text',text:JSON.stringify(mojo)}]},
    {role:'assistant',stopReason:'stop',content:[{type:'text',text:'Done.'}]}];
  const snapshot={type:'bridge.snapshot',snapshot:{pi:{inbox:[],entries:[...messages.map(m=>({model:[m]})),...Array.from({length:3},()=>({kind:'glimmer.profile.used',data:{profile:{digest:'test-profile'}}}))]}}};
  return {bytes,mojo,messages,snapshot};
}
test('both EOF forms pass without changing the actual source hash contract',()=>{
  for(const newline of [true,false]){const f=fixture(newline);assert.equal(outcome(f.snapshot,f.bytes,profile,source,'ok').mojo.run.exit_code,0);}
});
test('actual source must equal the recorded write and the intended body',()=>{
  const f=fixture();assert.throws(()=>outcome(f.snapshot,Buffer.from(source+'\n'),profile,source,'ok'));
  assert.throws(()=>outcome(f.snapshot,f.bytes,profile,source.replace('"ok"','"different"'),'ok'));
  f.messages[0].content[0].arguments.content='different';assert.throws(()=>outcome(f.snapshot,f.bytes,profile,source,'ok'));
});
test('failure, invented output, stale source, and absent isolation evidence are rejected',()=>{
  for(const fault of ['exit','stdout','hash','empty_checks','false_check']){
    const f=fixture();if(fault==='exit')f.mojo.run.exit_code=1;if(fault==='stdout')f.mojo.run.stdout='invented';
    if(fault==='hash')f.mojo.compile.metadata.source_sha256='wrong';if(fault==='empty_checks')f.mojo.run.evidence.isolation.checks={};
    if(fault==='false_check')f.mojo.run.evidence.isolation.checks.work_ro=false;
    f.messages[3].content[0].text=JSON.stringify(f.mojo);assert.throws(()=>outcome(f.snapshot,f.bytes,profile,source,'ok'),fault);
  }
});
test('missing tool result, wrong profile, or an unsettled run cannot pass',()=>{
  for(const fault of ['tool','profile','busy']){
    const f=fixture();if(fault==='tool')f.messages[3].toolCallId='other';
    if(fault==='profile')f.snapshot.snapshot.pi.entries.at(-1).data.profile.digest='other';
    if(fault==='busy')f.snapshot.snapshot.pi.run={};assert.throws(()=>outcome(f.snapshot,f.bytes,profile,source,'ok'),fault);
  }
});
