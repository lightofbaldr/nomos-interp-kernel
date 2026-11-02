import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {assertSupportedRuntime} from './runtime.mjs';
import {loadRecordedHarness} from '../src/lib/server/recorded-harness.ts';
import {recordedHarnessConfig as cfg} from '../src/lib/server/recorded-harness-config.ts';
assertSupportedRuntime();
// Real local recordings, read-only. Independent recount from the aggregate files.
const r=await loadRecordedHarness();assert.deepEqual(r.results,{base:1,improved:8,total:10});
assert.equal(r.task,'generics-largest-element');assert.equal(r.base.events.length,6);assert.equal(r.improved.events.length,15);
for(const key of ['base','improved']){
  const arm=r[key],source=cfg[key],aggregate=JSON.parse(await readFile(`${cfg.root}/mojo/evaluate-test-${source.id}-driver.json`));
  assert.equal(aggregate.rows.filter(x=>x.score.ground_truth.passed===true).length,r.results[key]);
  assert.equal(arm.harnessSha256,source.sha256);assert.equal(arm.source,await readFile(`${cfg.root}/mojo/executions/evaluate-test-${source.id}/${cfg.task}/solution.mojo`,'utf8'));
  const trace=(await readFile(`${cfg.root}/mojo/executions/evaluate-test-${source.id}/${cfg.task}/pi.jsonl`,'utf8')).trim().split('\n').map(x=>JSON.parse(x));
  assert.deepEqual(arm.events.map(x=>[x.name,x.args,x.output,x.isError]),trace.filter(x=>x.type==='tool_execution_end').map(x=>[x.toolName,trace.find(s=>s.type==='tool_execution_start'&&s.toolCallId===x.toolCallId).args,x.result.content.filter(c=>c.type==='text').map(c=>c.text).join('\n'),x.isError]));
}
const checks=r.improved.events.filter(e=>e.name==='bash'&&String(e.args.command??'').includes('check_mojo.py'));
assert.equal(checks.length,4);assert.ok(checks.every(x=>x.isError));assert.match(checks.at(-1).output,/CHECK INCOMPLETE: compiled, but no code ran/);
assert.equal(r.base.events.filter(e=>e.name==='bash'&&/(?:^|\s)mojo\s+(?:run|build)|check_mojo\.py/.test(String(e.args.command))).length,0);
assert.equal(r.base.grade.compile.returncode,1);assert.equal(r.base.grade.run,null);assert.equal(r.improved.grade.compile.returncode,0);assert.equal(r.improved.grade.run.returncode,0);assert.equal(r.improved.grade.run.stdout,'ok\n');
assert.equal(r.tasks.filter(t=>!t.base.passed&&t.improved.passed).length,7);assert.equal(r.tasks.filter(t=>t.base.passed&&!t.improved.passed).length,0);
assert.deepEqual(r.changes.filter(f=>f.scaffold).map(f=>f.path),['extensions/README.md','scripts/README.md','skills/README.md','sub_agents/README.md']);
const loaded=r.changes.filter(f=>!f.scaffold);
assert.deepEqual(loaded.filter(f=>f.kind==='added').map(f=>f.path),['extensions/mojo-verify-note.ts','skills/mojo-simd/SKILL.md','skills/mojo-verify/SKILL.md','skills/mojo-verify/scripts/check_mojo.py']);
const changes=Object.fromEntries(['added','changed','unchanged','removed'].map(kind=>[kind,loaded.filter(f=>f.kind===kind).length]));
assert.deepEqual(changes,{added:4,changed:2,unchanged:3,removed:0});
console.log(JSON.stringify({inputs:Object.keys(r.inputs).length,results:r.results,tools:[6,15],selfCheck:'compiled; no code ran',grader:'separate PASS',changes,scaffoldNotes:4}));
console.log('RECORDED WORKBENCH ASSETS VERIFIED');
