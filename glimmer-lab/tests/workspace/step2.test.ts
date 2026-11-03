import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,rename,symlink,rm} from 'node:fs/promises';
import {fixture} from './step2-fixture.ts';
import {pinnedBytes,sha256} from '../../src/lib/server/pinned-recording.ts';
import {answerWindows} from '../../src/lib/workspace/step2.ts';
import {candidates,validateWorkspaceTurn} from '../../src/lib/workspace/model.ts';
const id='step2-default-000000000000';

test('48 choices group by item then setup; selected capture carries exact prompt and captured identities',async()=>{
  const f=await fixture();try{
    const list=await f.source.catalog();assert.equal(list.length,48);assert.equal(new Set(list.map(x=>x.id)).size,48);assert.equal(new Set(list.slice(0,6).map(x=>x.item)).size,1);
    const t=await f.source.recording('step2-kind-000000000000');assert.equal(t.prompt,'Question 0?');assert.equal(t.step2?.system,'System kind');assert.equal(t.step2?.opener,'Opener kind');assert.equal(t.step2?.user,'Opener kind\n\nQuestion 0?');assert.equal(t.steering,'No steering');assert.equal(t.step2?.assets.lens,'e'.repeat(64));assert.equal(t.step2?.sample,0);
    assert.deepEqual(answerWindows(t.records,t.step2!),{registered:Array.from({length:64},(_,i)=>i+1),taskStart:66,answerCount:70});
    const short=await f.source.recording('step2-calibrated-000000000000');assert.equal(answerWindows(short.records,short.step2!).registered.length,15);
    const raw=candidates(t,t.records[1],48)!;assert.equal(raw.length,16);assert.equal(raw[0].rank,1);assert.ok(raw[0].hiddenReason);assert.equal(raw.filter(c=>!c.hiddenReason)[0].rank,2,'Display filtering cannot renumber the original top 8');
    const lens=await f.source.recording('step2-fact_checker-e7e6accc4bcd');assert.match(lens.step2!.classifier.warning!,/character 764.*opening refusal/);
    assert.equal((await f.source.recording(id)).step2!.classifier.warning,null);
    const evidence=await f.source.precheckEvidence();assert.match(evidence.scope,/one default scenario, not 48/);assert.equal(evidence.gates.default.G1_repeat.pass,true);
  }finally{await f.close();}
});

test('catalog reads metadata only; one recording never opens other captures',async()=>{
  const f=await fixture();try{await rm(f.root+'/step2/capture-20261101T122747/cold-000000000000',{recursive:true});assert.equal((await f.source.catalog()).length,48);assert.equal((await f.source.recording(id)).id,id);await assert.rejects(f.source.recording('step2-cold-000000000000'));}finally{await f.close();}
});

test('every metadata input is pinned; no changed file is silently substituted',async t=>{
  for(const file of ['step2/recordings-manifest.json','step2/task-response-start.json','step2/scenarios.json','items.json','conditions.json','step2/capture-20261101T122747/identity.json'])await t.test(file,async()=>{
    const f=await fixture();try{await writeFile(f.root+'/'+file,'{}');await assert.rejects(f.source.catalog(),/identity changed/);}finally{await f.close();}
  });
});

test('hash-checked metadata still rejects duplicate, missing, misjoined or out-of-range records',async t=>{
  const cases:[string,string,(x:any)=>void][]=[
    ['duplicate','step2/recordings-manifest.json',x=>x.recordings[1]=x.recordings[0]],
    ['missing','step2/recordings-manifest.json',x=>x.recordings.pop()],
    ['path','step2/recordings-manifest.json',x=>x.recordings[0].path='../secret'],
    ['question','items.json',x=>x[0].user='Another question'],
    ['system','conditions.json',x=>x.default.system='Another system'],
    ['marker duplicate','step2/task-response-start.json',x=>x.turns[1]=x.turns[0]],
    ['marker index','step2/task-response-start.json',x=>x.turns[0].answer_token_index=x.turns[0].answer_tokens],
    ['marker wrong count','step2/task-response-start.json',x=>x.turns[0].answer_tokens++],
    ['marker scope','step2/task-response-start.json',x=>x.meta.selected_post_hoc=false],
    ['scenario wrong binding','step2/scenarios.json',x=>x[0].source.system_sha256='0'.repeat(64)]
  ];
  for(const [name,file,edit] of cases)await t.test(name,async()=>{const f=await fixture();try{const obj=JSON.parse(await readFile(f.root+'/'+file,'utf8'));edit(obj);await f.put(file,obj);await assert.rejects(f.source.recording(id));}finally{await f.close();}});
});

test('unlisted IDs and all path forms refuse, and bound captures/tokenizer cannot change',async()=>{
  const f=await fixture();try{
    for(const bad of ['../../.env','/etc/passwd','step2-default-ffffffffffff','step2-default-000000000000/../x'])await assert.rejects(f.source.recording(bad));
    await writeFile(f.cfg.tokenizer,'{}');await assert.rejects(f.source.recording(id),/identity changed/);
    const manifest=JSON.parse(await readFile(f.root+'/step2/recordings-manifest.json','utf8'));
    await writeFile(f.root+'/step2/'+manifest.recordings[0].path,'{}');await assert.rejects(f.source.recording(id),/identity changed/);
  }finally{await f.close();}
});

test('bound capture metadata is checked against the scenario, not just its own fields',async()=>{
  const f=await fixture();try{
    const manifest=JSON.parse(await readFile(f.root+'/step2/recordings-manifest.json','utf8')),entry=manifest.recordings[0];
    const doc=JSON.parse(await readFile(f.root+'/step2/'+entry.path,'utf8'));doc.identity.lens='f'.repeat(64);
    entry.sha256=await f.save('step2/'+entry.path,doc);await f.put('step2/recordings-manifest.json',manifest);
    await assert.rejects(f.source.recording(id),/metadata mismatch/);
  }finally{await f.close();}
});

test('no-follow reader rejects traversal, symlink file/parent/root, non-file, cap and hash mismatch',async()=>{
  const f=await fixture();try{
    const pin=await f.save('safe/a.json',{value:1});assert.equal(sha256(await pinnedBytes(f.root,'safe/a.json',pin,20)),pin);
    for(const rel of ['../safe/a.json','safe/../safe/a.json','/safe/a.json','safe//a.json','safe/./a.json','safe\\a.json'])await assert.rejects(pinnedBytes(f.root,rel,pin,20));
    await assert.rejects(pinnedBytes(f.root,'safe/a.json',pin,1));await assert.rejects(pinnedBytes(f.root,'safe/a.json','0'.repeat(64),20));
    await symlink(f.root+'/safe/a.json',f.root+'/link.json');await assert.rejects(pinnedBytes(f.root,'link.json',pin,20),/Symlink/);
    await symlink(f.root+'/safe',f.root+'/linked');await assert.rejects(pinnedBytes(f.root,'linked/a.json',pin,20),/Symlink/);
    await assert.rejects(pinnedBytes(f.root+'/linked','a.json',pin,20),/Symlink/);
    await assert.rejects(pinnedBytes(f.root,'safe',pin,20));
  }finally{await f.close();}
});

test('real adapter refuses a symlinked selected capture; precheck evidence is pinned too',async()=>{
  const f=await fixture();try{
    const manifest=JSON.parse(await readFile(f.root+'/step2/recordings-manifest.json','utf8')),file=f.root+'/step2/'+manifest.recordings[0].path;
    await rename(file,file+'.original');await symlink(file+'.original',file);await assert.rejects(f.source.recording(id),/Symlink/);
    await writeFile(f.root+'/step2/precheck-20261101T122407/gates.json','{}');await assert.rejects(f.source.precheckEvidence(),/identity changed/);
  }finally{await f.close();}
});

test('client contract also refuses changed marker coverage',async()=>{
  const f=await fixture();try{const t=await f.source.recording(id);t.step2!.marker.answerTokens++;assert.throws(()=>validateWorkspaceTurn(t));}finally{await f.close();}
});
