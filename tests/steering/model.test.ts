import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { topCandidates, differenceGroups, scoreText, deltaText, deltaClass, numberText, boundText, tokenText, parseBundle, parseIndex, safePath } from '../../src/lib/steering-diff/model.ts';
import type { Candidate } from '../../src/lib/steering-diff/model.ts';
const exact: Candidate = { id: 1, baseline: 12, steered: 10, baseline_rank: 15, steered_rank: 4, baseline_below: null, steered_below: null, delta: -2, delta_min: null, delta_max: null, status: 'entered' };
const bound: Candidate = { ...exact, id: 2, baseline: null, baseline_rank: null, baseline_below: 3, delta: null, delta_min: 7 };
test('top 8 is not the whole transmitted top 16', () => { assert.equal(topCandidates([exact], 'baseline',8).length, 0); assert.equal(topCandidates([exact], 'steered',8).length, 1); });
test('entered is a membership label, not positive movement', () => { assert.equal(deltaClass(exact), 'decrease'); assert.equal(deltaText(exact), '-2.00'); });
test('unknown score is displayed as a bound, never zero', () => { assert.equal(scoreText(bound,'baseline'), '≤ 3.00'); assert.equal(deltaText(bound), '≥ +7.00'); });
test('bounds round outward so formatting does not strengthen them', () => { assert.equal(boundText(1.239,true),'1.23'); assert.equal(boundText(-1.239,false),'-1.23'); assert.equal(boundText(1.231,false),'1.24'); assert.equal(boundText(-1.231,true),'-1.24'); });
test('left unknown and two-sided bounds stay bounded', () => { assert.equal(deltaText({...bound, delta_min:null, delta_max:-7}), '≤ -7.00'); assert.equal(deltaText({...bound, delta_max:10}), '[+7.00, +10.00]'); assert.equal(deltaText({...bound, delta_min:null}), 'Unknown'); });
test('exact deltas sorted signed; bounds kept separate', () => { const g=differenceGroups([exact,bound,{...exact,id:3,delta:4}]); assert.deepEqual(g.exact.map(c=>c.id),[3,1]); assert.deepEqual(g.bounded.map(c=>c.id),[2]); });
test('zero is distinct from missing and tiny changes are not rounded to zero', () => { assert.equal(numberText(0),'0.00'); assert.equal(numberText(null),'Unknown'); assert.match(numberText(.000001,true), /e-6/); assert.throws(()=>numberText(NaN)); });
test('token labels preserve whitespace and missing vocabulary', () => { assert.equal(tokenText({'1':' a\n\t'},1),'␠a↵⇥'); assert.match(tokenText({},3), /unavailable/); });
test('unsafe paths refused', () => { for (const p of ['../x','/x','a/../x','a//b','a?b','a\\b']) assert.equal(safePath(p),false); assert.equal(safePath('S1/joy_0.7.free.json'),true); });
const root = (process.env.GLIMMER_STEERDIFF_DATA_DIR ?? '/home/adam/Projects/glimmer-steering-diff-scratch-20261026/public/data') + '/dataset-1';
const idx=parseIndex(JSON.parse(fs.readFileSync(root+'/index.json','utf8')));
const ref=idx.comparisons.find(r=>r.arm==='joy_0.7' && r.mode==='free')!;
const bundle=JSON.parse(fs.readFileSync(root+'/'+ref.path,'utf8'));
test('real free bundle contains no comparison after divergence', () => { const b=parseBundle(bundle,idx,ref); assert.ok(b.comparison.positions.every(p=>p.pos < b.comparison.divergence!.position)); assert.ok(b.comparison.tracks); assert.ok(b.comparison.tracks.baseline.length); });
test('malformed inputs never masquerade as valid comparisons', () => {
  for (const mutate of [(b:any)=>b.mode='matched',(b:any)=>b.comparison.positions[0].emotion_delta[0][0]=Infinity,(b:any)=>b.comparison.positions.at(-1).pos=b.comparison.divergence.position,(b:any)=>b.comparison.positions[55].workspace[0].candidates[0].baseline_rank=17]) {
    const b=structuredClone(bundle); mutate(b); assert.throws(()=>parseBundle(b,idx,ref));
  }
});
test('index duplicate comparisons and changed setting refuse', () => { const i=structuredClone(idx); i.comparisons.push(i.comparisons[0]); assert.throws(()=>parseIndex(i)); const b=structuredClone(bundle); b.comparison.display_k=16; assert.throws(()=>parseBundle(b,idx,ref)); });
test('adapter never modifies source values', () => { const before=JSON.stringify(bundle); const b=parseBundle(bundle,idx,ref); const candidates=b.comparison.positions[55].workspace![0].candidates; topCandidates(candidates,'steered',8); differenceGroups(candidates); assert.equal(JSON.stringify(bundle),before); });
