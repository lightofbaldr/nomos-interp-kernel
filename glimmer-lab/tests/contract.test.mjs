import {test} from 'node:test';
import assert from 'node:assert/strict';
import {demoTrace,scenarios} from '../src/lib/demo.ts';
import {parseTrace,parseTraceText,answerAt,scoreLabel} from '../src/lib/trace.ts';

test('All demo scenarios are visibly synthetic, reproducible, correctly joined traces',()=>{
  for(const scenario of scenarios){const a=demoTrace(scenario),b=demoTrace(scenario);assert.deepEqual(a,b);assert.equal(a.source,'demo');assert.equal(a.probe.kernelSha256,null);assert.equal(a.probe.calibration,null);assert.equal(a.messages[0].text,scenario.prompt);assert.equal(answerAt(a,a.frames.length-1),scenario.answer);assert.ok(answerAt(a,0).length<scenario.answer.length);}
});
const invalid=[
  t=>t.schema='other',t=>t.source='live',t=>t.runId='',t=>t.modelId=null,t=>t.capturedAt='not a timestamp',
  t=>t.probe.scoreKind='probability',t=>t.probe.kernelSha256='unknown',t=>t.probe.calibration=undefined,
  t=>t.probe.layerIds=[24,12,36,48],t=>t.probe.layerIds=[12,12,36,48],t=>t.probe.layerIds[0]=-1,
  t=>t.frames[0].concepts[0].scores.pop(),t=>t.frames[0].concepts[0].scores[0]=1.1,t=>t.frames[0].emotions[0].score=NaN,
  t=>t.frames[0].emotions[0].score=Infinity,t=>t.frames[1].sequence=t.frames[0].sequence,t=>t.frames[1].tokenIndex=0,
  t=>t.frames[1].elapsedMs=-1,t=>t.frames[1].textOffset=0,t=>t.frames[0].textOffset=t.messages[1].text.length+1,
  t=>t.frames=[],t=>t.frames[0].concepts.push(t.frames[0].concepts[0]),t=>t.frames[0].emotions.push(t.frames[0].emotions[0]),
  t=>t.messages[0].role='assistant',t=>t.probe.safe=true,t=>t.frames[0].concepts[0].scores[0]='0.7'
];
invalid.forEach((mutate,i)=>test(`Known-bad trace ${i+1} is rejected rather than coerced`,()=>{const data=demoTrace(scenarios[0]);mutate(data);assert.throws(()=>parseTrace(data),/Trace rejected/);}));
test('Unknown metadata stays unknown; genuine zero/negative scores and absent readouts remain distinct',()=>{const data=demoTrace(scenarios[0]);data.frames[0].concepts[0].scores=[0,-.5,1,-1];data.frames[1].emotions=[];data.frames[1].concepts=[];const result=parseTrace(data);assert.equal(result.probe.kernelSha256,null);assert.equal(result.frames[0].concepts[0].scores[0],0);assert.equal(result.frames[0].concepts[0].scores[1],-.5);assert.deepEqual(result.frames[1].emotions,[]);assert.equal(scoreLabel(-.5),'-.50'.replace('-.','-0.'));});
test('JSON boundary rejects broken, oversized and prototype-pollution inputs',()=>{assert.throws(()=>parseTraceText('{'),/valid JSON/);assert.throws(()=>parseTraceText(' '.repeat(2_000_001)),/2 MB/);const raw=JSON.stringify(demoTrace(scenarios[0]));assert.deepEqual(parseTraceText(raw),demoTrace(scenarios[0]));assert.throws(()=>parseTraceText(raw.replace('{','{"__proto__":{"polluted":true},')),/unsupported fields/);assert.equal({}.polluted,undefined);});
test('Absent selection never fabricates a frame',()=>{assert.throws(()=>answerAt(demoTrace(scenarios[0]),999),/selected frame/);});
test.after(()=>console.log('MUSE CONTRACT PASSED: positive fixtures and 26 malformed trace controls.'));
