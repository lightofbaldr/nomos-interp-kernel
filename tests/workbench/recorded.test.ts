import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,rename,symlink,rm} from 'node:fs/promises';
import {fixture} from './recorded-fixture.ts';
import {harnessDigest,parseRecordedTrace} from '../../src/lib/server/recorded-harness.ts';
import {pinnedBytes,sha256} from '../../src/lib/server/pinned-recording.ts';
import {harnessChanges} from '../../src/lib/workbench/recorded.ts';

test('pinned pair retains exact prompt, tool exchanges, source, grades, all ten outcomes and harness changes',async()=>{
  const f=await fixture();try{const r=await f.load();assert.deepEqual(r.results,{base:0,improved:10,total:10});assert.equal(r.base.events[0].args.content,r.base.source);assert.equal(r.base.events[0].startLine,4);assert.equal(r.base.events[0].endLine,5);assert.equal(r.base.grade.run,null);assert.equal(r.improved.grade.run?.stdout,'ok\n');assert.equal(r.changes.find(x=>x.path==='skills/mojo-verify/SKILL.md')?.kind,'added');assert.equal(r.changes.find(x=>x.path==='guardrails.md')?.base?.text,'');assert.equal(r.base.harnessSha256,f.cfg.base.sha256);assert.equal(r.improved.files.at(-1)?.text,'Fixture verification skill <script>globalThis.BAD=true</script>');}finally{await f.close();}
});
test('every consumed input is pinned and checked again on the next request',async()=>{
  const f=await fixture();try{await f.load();for(const file of Object.keys(f.cfg.pins)){const old=await readFile(f.root+'/'+file);await writeFile(f.root+'/'+file,'changed');await assert.rejects(f.load(),/identity changed/);await writeFile(f.root+'/'+file,old);}}finally{await f.close();}
});
test('harness digest matches Python sorted JSON, handles unicode/newlines and excludes README stubs',()=>{
  const f={path:'systemprompt.md',text:'é\r\n',sha256:'ignored'},stub={path:'scripts/README.md',text:'not part of executor tree',sha256:'ignored'};
  assert.equal(harnessDigest([f,stub]),sha256('{"systemprompt.md": "\\u00e9\\n"}'));
});
test('file changes distinguish absent, empty, removed and unchanged',()=>{
  const file=(path:string,text:string)=>({path,text,sha256:sha256(text)});
  const r=harnessChanges([file('a',''),file('b','1'),file('c','')],[file('a',''),file('b','2'),file('d','')]);
  assert.deepEqual(r.map(f=>f.kind),['unchanged','changed','removed','added']);assert.equal(r[2].improved,null);assert.equal(r[3].improved?.text,'');
});
test('all identity links refuse inconsistencies even with updated file pins',async t=>{
  const cases:[string,string,(x:any)=>void][]=[
    ['duplicate task','split-run2.json',x=>x.test[1]=x.test[0]],
    ['missing task','split-run2.json',x=>x.test.pop()],
    ['aggregate harness','mojo/evaluate-test-base-driver.json',x=>x.harness_sha256='a'.repeat(64)],
    ['aggregate missing','mojo/evaluate-test-base-driver.json',x=>x.rows.pop()],
    ['aggregate duplicate','mojo/evaluate-test-base-driver.json',x=>x.rows[1]=x.rows[0]],
    ['aggregate reorder','mojo/evaluate-test-base-driver.json',x=>x.rows.reverse()],
    ['grade mismatch','mojo/evaluate-test-base-driver.json',x=>x.rows[2].score.ground_truth.reason='different'],
    ['completion identity','complete.json',x=>x.identity.model='other'],
    ['completion missing','complete.json',x=>delete x.outputs['pi.jsonl']],
    ['completion extra','complete.json',x=>x.outputs['other']='a'.repeat(64)],
    ['trace binding','complete.json',x=>x.outputs['pi.jsonl']='a'.repeat(64)],
    ['launch task','launch.json',x=>x.identity.task='a'.repeat(64)],
    ['launch prompt','launch.json',x=>x.argv[x.argv.length-1]='Different prompt'],
    ['grade source','mojo-grade.json',x=>x.ground_truth.code_sha256='a'.repeat(64)]
  ];
  for(const [label,file,edit] of cases)await t.test(label,async()=>{const f=await fixture();try{await f.edit(file.includes('/')||file==='split-run2.json'?file:f.prefix()+file,edit);await assert.rejects(f.load());}finally{await f.close();}});
});
test('trace counters and delivered/settled status are not inferred from a zero process exit',async t=>{
  for(const [label,edit] of [
    ['delivered',(x:any)=>x.delivered=false],['settled',(x:any)=>x.usage.settled=false],['aborted',(x:any)=>x.usage.aborted=true],['tool count',(x:any)=>x.usage.tool_calls++],['model count',(x:any)=>x.model_calls++],['turn count',(x:any)=>x.usage.turns++],['wall time',(x:any)=>x.seconds=-1]
  ] as const)await t.test(label,async()=>{const f=await fixture();try{await f.edit(f.prefix()+'result.json',edit);await f.rebind();await assert.rejects(f.load());}finally{await f.close();}});
});
test('malformed event relationships refuse before presenting an apparent successful trace',async t=>{
  const f=await fixture();try{
    const raw=await readFile(f.root+'/'+f.prefix()+'pi.jsonl','utf8'),rows=raw.trim().split('\n').map(x=>JSON.parse(x));
    for(const [label,edit] of [
      ['start before assistant',(a:any[])=>[a[2],a[3]]=[a[3],a[2]]],
      ['duplicate start',(a:any[])=>a.splice(4,0,a[3])],
      ['no end',(a:any[])=>a.splice(4,1)],
      ['duplicate end',(a:any[])=>a.splice(5,0,a[4])],
      ['wrong tool',(a:any[])=>a[4].toolName='bash'],
      ['different args',(a:any[])=>a[3].args={path:'/elsewhere'}],
      ['different result',(a:any[])=>a[5].message.content[0].text='substitution'],
      ['missing result',(a:any[])=>a.splice(5,1)],
      ['unsettled',(a:any[])=>a.pop()],
      ['aborted',(a:any[])=>a.at(-1).aborted=true],
      ['invalid error flag',(a:any[])=>a[4].isError='false'],
      ['no final',(a:any[])=>a[6].message.content=[]]
    ] as const)await t.test(label,()=>{const a=structuredClone(rows);edit(a);assert.throws(()=>parseRecordedTrace(a.map(x=>JSON.stringify(x)).join('\n'),'Fixture request: write the requested definitions.'));});
  }finally{await f.close();}
});
test('a missing program remains a failed task, with unknown reason explicit',async()=>{
  const f=await fixture();try{await f.edit('mojo/evaluate-test-base-driver.json',x=>x.rows[1]={task:'task-1',rollout:'failed',model_calls:32,reason:null,score:{ground_truth:{status:'rollout_failed',passed:false},muse_quality:null}});const r=await f.load();assert.deepEqual(r.tasks[1].base,{passed:false,rollout:'failed',modelCalls:32,reason:null});}finally{await f.close();}
});
test('contradictory grader outputs refuse, including booleans-as-numbers',async t=>{
  for(const [label,edit] of [
    ['compile failure on pass',(s:any)=>s.ground_truth.compile.returncode=1],
    ['missing run on pass',(s:any)=>s.ground_truth.run=null],
    ['quality substituted',(s:any)=>s.quality_not_in_ground_truth=false],
    ['numerical bool',(s:any)=>s.ground_truth.compile.returncode=true],
    ['string pass',(s:any)=>s.ground_truth.passed='true'],
    ['criteria mismatch',(s:any)=>s.n_criteria=2],
    ['toolchain fails',(s:any)=>s.ground_truth.toolchain_health.pass=false],
    ['failed rollout says pass',(s:any)=>{s.all_pass=false;}]
  ] as const)await t.test(label,async()=>{const f=await fixture();try{
    await f.edit(f.prefix('improved')+'mojo-grade.json',edit);const grade=await f.read(f.prefix('improved')+'mojo-grade.json');await f.edit('mojo/evaluate-test-improved-driver.json',x=>x.rows[2].score=grade);await assert.rejects(f.load());
  }finally{await f.close();}});
});
test('file pins do not replace the complete harness identity',async()=>{
  const f=await fixture();try{await f.put('frozen/base/systemprompt.md','Different instructions');await assert.rejects(f.load(),/full harness identity/);}finally{await f.close();}
});
test('new empty-text option is explicit; traversal, symlinks, missing files and caps still refuse',async()=>{
  const f=await fixture();try{
    const file='frozen/base/guardrails.md',hash=f.cfg.pins[file];await assert.rejects(pinnedBytes(f.root,file,hash,10),/size/);assert.equal((await pinnedBytes(f.root,file,hash,10,true)).length,0);
    for(const name of ['../.env','/etc/passwd','frozen//base/systemprompt.md','frozen/base/../base/systemprompt.md'])await assert.rejects(pinnedBytes(f.root,name,hash,10,true));
    await rename(f.root+'/frozen/base',f.root+'/frozen/original');await symlink(f.root+'/frozen/original',f.root+'/frozen/base');await assert.rejects(f.load(),/Symlink/);
    await rm(f.root+'/frozen/base');await rename(f.root+'/frozen/original',f.root+'/frozen/base');
    const target=f.prefix()+'solution.mojo';await rm(f.root+'/'+target);await assert.rejects(f.load());
    await f.put(target,'x'.repeat(1024*1024+1));await assert.rejects(f.load(),/size/);
  }finally{await f.close();}
});
