import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {step2Source,step2Config} from '../src/lib/server/workspace-step2.ts';
import {answerWindows,firstStep2Recording} from '../src/lib/workspace/step2.ts';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
// Read-only, sequential reconciliation against operator-supplied captures.
// No inference, tokenizer service, live registry or score computation.
const manifest=JSON.parse(await readFile(step2Config.root+'/step2/recordings-manifest.json','utf8'));
const markers=JSON.parse(await readFile(step2Config.root+'/step2/task-response-start.json','utf8'));
const catalog=await step2Source.catalog();assert.equal(catalog.length,48);
assert.equal(catalog[0].id,firstStep2Recording);
let rows=0,minWindow=64,maxWindow=0;
for(const choice of catalog){
  const turn=await step2Source.recording(choice.id),key=turn.step2.key;
  const binding=manifest.recordings.find(x=>x.key===key),marker=markers.turns.find(x=>x.key===key);
  const raw=JSON.parse(await readFile(step2Config.root+'/step2/'+binding.path,'utf8'));
  assert.deepEqual(turn.records,raw.records,'Every position, candidate ID, rank and score is preserved');
  assert.equal(turn.step2.classifier.registered,binding.registered_label);assert.equal(turn.step2.classifier.sensitivity,binding.corrected_label);
  assert.equal(turn.step2.marker.anchor,marker.anchor);assert.equal(turn.step2.marker.charOffset,marker.char_offset);
  const window=answerWindows(turn.records,turn.step2),answer=raw.records.filter(x=>x.source==='generated'&&x.channel==='answer');
  assert.deepEqual(window.registered,answer.slice(0,64).map(x=>x.pos));assert.equal(window.taskStart,answer[marker.answer_token_index].pos);
  assert.equal(window.answerCount,marker.answer_tokens);minWindow=Math.min(minWindow,window.registered.length);maxWindow=Math.max(maxWindow,window.registered.length);rows+=turn.records.length;
}
assert.match((await step2Source.precheckEvidence()).scope,/not 48 repeat checks/);
console.log('STEP2 ASSETS VERIFIED',JSON.stringify({recordings:catalog.length,rows,registeredWindow:{min:minWindow,max:maxWindow},modelCalls:0}));
