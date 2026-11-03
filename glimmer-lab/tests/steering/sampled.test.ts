import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {homedir} from 'node:os';
import {parseIndex,parseBundle,freePrefill} from '../../src/lib/steering-diff/model.ts';

// Producer-owned, read-only local bundles. No model/capture work in these tests.
const root=path.join(homedir(),'nomos_data/steering-diff');
const runs=['run-20261027T090425-ws-SIETTI','run-20261027T090425-ws-TATHUGA','run-20261026T205808-sampled-TATHUGA'];
const load=(run:string)=>{const dir=path.join(root,run,'ui'),index=JSON.parse(fs.readFileSync(path.join(dir,'index.json'),'utf8'));return {index,read:(ref:any)=>JSON.parse(fs.readFileSync(path.join(dir,ref.path),'utf8'))};};
test('all three curated sampled bundles validate with explicit same-sample arms, including emotion-only captures',()=>{
  let n=0;for(const run of runs){const {index,read}=load(run);parseIndex(index);for(const ref of index.comparisons){const raw=read(ref),bytes=JSON.stringify(raw),b=parseBundle(raw,index,ref);n++;
    assert.equal(b.sample,ref.sample);assert.equal(b.comparison.baseline.run_id,`${ref.prompt}.base.s${ref.sample}`);assert.equal(b.comparison.steered.run_id,`${ref.prompt}.${ref.arm}.s${ref.sample}`);assert.equal(JSON.stringify(b),bytes);
    if(index.settings.workspace===null)assert.ok(b.comparison.positions.every(p=>p.workspace===null));
  }}assert.equal(n,25);
});
test('sample identity cannot be silently dropped, duplicated, relabelled or mixed between arms',()=>{
  const {index,read}=load(runs[0]);const ref=index.comparisons.find((r:any)=>r.arm==='fear_0.5'&&r.sample===1),raw=read(ref);
  for(const mutate of [(b:any)=>delete b.sample,(b:any)=>b.sample=0,(b:any)=>b.comparison.baseline.run_id=b.comparison.baseline.run_id.replace('.s1','.s0'),(b:any)=>b.comparison.steered.run_id=b.comparison.steered.run_id.replace('.s1','.s0'),(b:any)=>b.comparison.baseline.arm.seed++,(b:any)=>b.comparison.baseline.arm.sample=0]){const b=structuredClone(raw);mutate(b);assert.throws(()=>parseBundle(b,index,ref),/sample/);}
  for(const mutate of [(i:any)=>i.comparisons.push(i.comparisons[0]),(i:any)=>i.comparisons[0].sample=true,(i:any)=>i.comparisons[0].sample=-1,(i:any)=>i.comparisons[0].path=i.comparisons[1].path]){const i=structuredClone(index);mutate(i);assert.throws(()=>parseIndex(i));}
});
test('labels stay attributed per arm; absent labels and source boundaries stay absent, not fabricated',()=>{
  const {index,read}=load(runs[0]);const ref=index.comparisons[0],raw=read(ref);const b=parseBundle(raw,index,ref);
  assert.equal(b.labels?.by,'Prime');assert.equal(b.labels?.baseline?.label,'lie');assert.equal(b.labels?.steered,null);assert.equal(b.scenario?.source?.answer_start,undefined);
  for(const mutate of [(b:any)=>b.labels.by=null,(b:any)=>b.labels.baseline='lie',(b:any)=>b.labels.baseline.label=true,(b:any)=>b.labels.steered={label:'honest',note:42}]){const c=structuredClone(raw);mutate(c);assert.throws(()=>parseBundle(c,index,ref),/label/);}
});
test('disabled workspace cannot carry invented workspace values and invalid supplied boundaries still refuse',()=>{
  const {index,read}=load(runs[2]),ref=index.comparisons[0],raw=read(ref);
  raw.comparison.positions[0].workspace=[];assert.throws(()=>parseBundle(raw,index,ref),/workspace/);
  index.scenarios[ref.prompt].source.answer_start=-1;assert.throws(()=>parseIndex(index),/answer_start/);
});
test('free-generation captions use declared boundaries rather than titles or the presence of a historical source',()=>{
  const {index}=load(runs[0]),s=index.scenarios['SIETTI-reason'];assert.equal(freePrefill(s),'prompt');
  const other=structuredClone(s);other.source.answer_start=1000;other.P=1000;assert.equal(freePrefill(other),'recorded-thinking');
  other.P=777;assert.equal(freePrefill(other),'unspecified');assert.equal(freePrefill(null),'unspecified');
});
