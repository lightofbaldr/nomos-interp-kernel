import test from 'node:test';
import assert from 'node:assert/strict';
import {fullResponseTarget,parseIndex} from '../../src/lib/steering-diff/model.ts';
import {scenarioFixture} from './scenario-fixture.ts';

test('replay opens only the same arm and source episode, not its fixed text',()=>{
  const {index}=scenarioFixture();parseIndex(index);
  const target=fullResponseTarget(index,'TEST-RECORDED','joy_minus',null);
  assert.equal(target.relation,'same-source');
  assert.equal(target.ref?.path,'TEST-CONTINUATION/joy_minus.free.json');
  assert.equal(target.ref?.mode,'free');
});
test('an exact free-generation scenario wins over linked alternatives',()=>{
  const {index}=scenarioFixture();
  const target=fullResponseTarget(index,'TEST-CONTINUATION','sad_minus',null);
  assert.equal(target.relation,'same-scenario');assert.equal(target.ref?.arm,'sad_minus');
});
test('same scenario can provide both modes without source metadata',()=>{
  const {index}=scenarioFixture();index.scenarios={};
  for(const ref of index.comparisons)if(ref.mode==='free')ref.prompt='TEST-RECORDED';
  assert.equal(fullResponseTarget(index,'TEST-RECORDED','joy_plus',null).relation,'same-scenario');
});
test('source path and sha must both match; titles and question text do not pair responses',()=>{
  for(const field of ['path','sha256'] as const){
    const {index}=scenarioFixture();index.scenarios!['TEST-CONTINUATION'].source={...index.scenarios!['TEST-CONTINUATION'].source!,[field]:'different'};
    assert.equal(fullResponseTarget(index,'TEST-RECORDED','joy_plus',null).ref,null);
  }
});
test('missing arm and missing free capture never substitute another answer',()=>{
  const {index}=scenarioFixture();
  for(const arm of ['', 'unknown'])assert.equal(fullResponseTarget(index,'TEST-RECORDED',arm,null).ref,null);
  index.comparisons=index.comparisons.filter(r=>r.mode==='matched'||r.arm!=='joy_minus');
  assert.equal(fullResponseTarget(index,'TEST-RECORDED','joy_minus',null).ref,null);
});
test('ambiguous source matches require explicit scenario selection',()=>{
  const {index}=scenarioFixture();
  index.scenarios!['OTHER-ANSWER']={...index.scenarios!['TEST-CONTINUATION'],key:'OTHER-ANSWER'};
  const free=index.comparisons.find(r=>r.mode==='free'&&r.arm==='joy_minus')!;
  index.comparisons.push({...free,prompt:'OTHER-ANSWER',path:'OTHER-ANSWER/joy_minus.free.json'});
  const target=fullResponseTarget(index,'TEST-RECORDED','joy_minus',null);
  assert.equal(target.ref,null);assert.match(target.reason!,/explicitly/);
});
test('sampled replies require the exact sample; null, missing and a different arm refuse',()=>{
  const {index}=scenarioFixture();
  index.comparisons=index.comparisons.filter(r=>r.mode==='free').flatMap(r=>[0,1].map(sample=>({...r,sample,path:`${r.prompt}/${r.arm}.s${sample}.free.json`})));
  parseIndex(index);
  assert.equal(fullResponseTarget(index,'TEST-CONTINUATION','joy_plus',null).ref,null);
  assert.equal(fullResponseTarget(index,'TEST-CONTINUATION','joy_plus',2).ref,null);
  assert.equal(fullResponseTarget(index,'TEST-CONTINUATION','joy_plus',1).ref?.path,'TEST-CONTINUATION/joy_plus.s1.free.json');
  index.comparisons=index.comparisons.filter(r=>r.arm!=='joy_plus'||r.sample!==1);
  assert.equal(fullResponseTarget(index,'TEST-CONTINUATION','joy_plus',1).ref,null);
});
test('unsampled replay never silently links a sampled generation',()=>{
  const {index}=scenarioFixture();
  for(const r of index.comparisons)if(r.mode==='free')r.sample=0;
  assert.equal(fullResponseTarget(index,'TEST-RECORDED','joy_minus',null).ref,null);
});
test('a missing free-generation reply cannot borrow from another answer scenario',()=>{
  const {index}=scenarioFixture();
  assert.equal(fullResponseTarget(index,'TEST-RECORDED','joy_minus',null,'free').ref,null);
  assert.equal(fullResponseTarget(index,'TEST-CONTINUATION','joy_minus',null,'free').relation,'same-scenario');
});
