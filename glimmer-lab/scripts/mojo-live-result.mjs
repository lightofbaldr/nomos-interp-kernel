import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const sha=b=>createHash('sha256').update(b).digest('hex');
const textOf=m=>m.content.filter(b=>b.type==='text').map(b=>b.text).join('');
export function inspect(s){
  assert.equal(s.type,'bridge.snapshot');const pi=s.snapshot.pi,entries=pi.entries,messages=entries.flatMap(e=>e.model??[]);
  const assistants=messages.filter(m=>m.role==='assistant');
  return {pi,entries,assistants,calls:assistants.flatMap(m=>m.content.filter(b=>b.type==='toolCall')),results:messages.filter(m=>m.role==='toolResult')};
}
export function outcome(s,bytes,profile,source,marker){
  const v=inspect(s);assert.ok(!v.pi.run&&!v.pi.generation&&!v.pi.inbox.length,'Run must settle');
  assert.deepEqual(v.calls.map(c=>c.name),['write','mojo'],'Exactly the intended tool sequence');
  for(const call of v.calls){const result=v.results.find(r=>r.toolCallId===call.id);assert.ok(result);assert.equal(result.isError,false);}
  // Check actual write -> disk -> compiler identity. A single optional EOF newline
  // does not change this smoke program; do not confuse it with a tool failure.
  assert.equal(bytes.toString(),v.calls[0].arguments.content);
  assert.equal(bytes.toString().replace(/\n$/,''),source.replace(/\n$/,''));
  assert.deepEqual(v.calls[1].arguments,{path:'main.mojo'});
  const mojo=JSON.parse(textOf(v.results.find(r=>r.toolCallId===v.calls[1].id)));
  const checks=['root_ro','runtime_ro','work_ro','build_rw','compiler_ro','config_ro','caps_zero','no_new_privs','home_empty','run_empty','cwd','env','fsize'];
  for(const phase of [mojo.compile,mojo.run]){
    assert.equal(phase.exit_code,0);assert.equal(phase.reason,null);assert.equal(phase.metadata.source_sha256,sha(bytes));
    assert.deepEqual(Object.keys(phase.evidence.isolation.checks).sort(),[...checks].sort());
    assert.ok(Object.values(phase.evidence.isolation.checks).every(x=>x===true));
    assert.equal(phase.evidence.resources.MemoryMax,'4294967296');assert.equal(phase.evidence.resources.MemorySwapMax,'0');
  }
  assert.equal(mojo.run.stdout,marker+'\n');
  const used=v.entries.filter(e=>e.kind==='glimmer.profile.used');assert.ok(used.length>=3);
  assert.ok(used.every(e=>e.data.profile.digest===profile.active.digest));
  assert.equal(v.assistants.at(-1).stopReason,'stop');
  return {calls:v.calls,mojo,modelResponses:v.assistants.length,profileDigest:profile.active.digest,profileUses:used.length,answer:textOf(v.assistants.at(-1)),sourceSha256:sha(bytes)};
}
