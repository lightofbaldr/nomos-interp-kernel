import test from 'node:test';
import assert from 'node:assert/strict';
import { armLabel, channelJumps, relatedScenarios, parseIndex, parseBundle } from '../../src/lib/steering-diff/model.ts';
import { scenarioFixture } from './scenario-fixture.ts';
test('each named direction preserves signed strength and asset name',()=>{
  for(const kind of ['joyful','calm','sad','afraid','angry','random1']) {
    assert.match(armLabel({name:kind,kind,s:.5}),/\+0\.5/);
    assert.match(armLabel({name:kind,kind,s:-.5}),/-0\.5 · counter-steered/);
  }
  assert.match(armLabel({name:'s',kind:'sad',s:.5}),/Sadness \[sad\]/);
  assert.match(armLabel({name:'f',kind:'afraid',s:-.5}),/Fear \[afraid\]/);
  assert.equal(armLabel({name:'zero',kind:'zero',s:0}),'Armed zero');
});
test('long scenario provenance is exact between index and bundle',()=>{
  const {index,makeBundle}=scenarioFixture();parseIndex(index);
  for(const ref of index.comparisons)parseBundle(makeBundle(ref),index,ref);
  const ref=index.comparisons[0], b=makeBundle(ref);b.scenario!.source!.label='different';assert.throws(()=>parseBundle(b,index,ref),/provenance/);
});
test('changed prefill or onset refuses even with identical scenario copies',()=>{
  const {index,makeBundle}=scenarioFixture(), ref=index.comparisons[0];
  const b=makeBundle(ref);b.comparison.P++;assert.throws(()=>parseBundle(b,index,ref),/prefill/);
  const c=makeBundle(ref);c.comparison.steered.arm.steer!.from_pos++;assert.throws(()=>parseBundle(c,index,ref),/onset/);
});
test('single-mode scenario cannot invent a second comparison mode',()=>{
  const {index}=scenarioFixture();index.scenarios!['TEST-RECORDED'].modes=['free'];assert.throws(()=>parseIndex(index),/undeclared/);
});
test('channel jumps navigate labels, not the original prompt or source boundary',()=>{
  const {index,makeBundle}=scenarioFixture(), ref=index.comparisons[0], b=makeBundle(ref);
  b.comparison.positions[12].channel='answer'; // A prefilled row is not a stepped answer.
  assert.deepEqual(channelJumps(b.comparison.positions),{reasoning:1953,answer:2209});
  const free=makeBundle(index.comparisons.find(r=>r.mode==='free')!);
  assert.deepEqual(channelJumps(free.comparison.positions),{reasoning:null,answer:2211});
  assert.deepEqual(channelJumps(free.comparison.positions.filter(p=>p.pos<2211)),{reasoning:null,answer:null});
});
test('scenario pairing uses source identity, not title, question or suffix',()=>{
  const {index}=scenarioFixture(), a=index.scenarios!['TEST-RECORDED'];
  assert.deepEqual(relatedScenarios(index,a).map(s=>s.key),['TEST-CONTINUATION']);
  index.scenarios!['TEST-CONTINUATION']={...index.scenarios!['TEST-CONTINUATION'],source:{...a.source!,sha256:'b'.repeat(64)}};
  assert.deepEqual(relatedScenarios(index,a),[]);
  assert.deepEqual(relatedScenarios(index,null),[]);
});
test('explicit no-scenario captures do not infer provenance from a lie-like name',()=>{
  const {index,makeBundle}=scenarioFixture(), ref=index.comparisons[0], b=makeBundle(ref);
  index.scenarios={}; b.scenario=null;parseIndex(index);parseBundle(b,index,ref);
  assert.equal(b.scenario,null);assert.deepEqual(relatedScenarios(index,null),[]);
});
