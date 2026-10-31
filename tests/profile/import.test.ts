import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,symlink,rename,link} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createImporter,snapshotHarness,importRoots} from '../../src/lib/server/profile-import.ts';
import {emptyContent} from 'glimmer-agent/profile-model';
async function fixture(){
  const root=await mkdtemp(path.join(os.tmpdir(),'lab-profile-import-'));
  await mkdir(root+'/h/skills/careful',{recursive:true});await mkdir(root+'/h/extensions');await mkdir(root+'/h/tool_descriptions');
  await writeFile(root+'/h/systemprompt.md','IMPORTED_INSTRUCTIONS');
  await writeFile(root+'/h/skills/careful/SKILL.md','---\nname: careful\ndescription: Think first\n---\nIMPORTED_SKILL');
  await writeFile(root+'/h/guardrails.md','Advisory, not enforcement.');await writeFile(root+'/h/tool_descriptions/bash.md','Unsupported bash guidance.');
  await writeFile(root+'/h/extensions/malicious.ts',`import {writeFileSync} from 'node:fs';writeFileSync(${JSON.stringify(root+'/SENTINEL')},'EXECUTED');\n<script>globalThis.bad=true</script>`);
  await writeFile(root+'/h/omitted.sh','exit 99');
  return {root,close:()=>rm(root,{recursive:true,force:true})};
}
test('import is an immutable text subset with explicit delivery, provenance, advisory sections, escaped preview data and omissions',async()=>{
  const f=await fixture();try{
    const service=createImporter([f.root]);const p=await service.preview('1',2,emptyContent('current'),0,'h');
    assert.match(p.origin.delivery,/on-demand.*inline/);assert.match(p.reference,/Current profile/);
    assert.equal(p.content.advisory.length,2);assert.equal(p.content.skills.length,1);assert.match(p.extensions[0].text,/writeFileSync/);
    assert.equal(p.files.find(f=>f.path==='omitted.sh')?.disposition,'omitted (not active)');
    assert.equal(p.files.find(f=>f.path==='extensions/malicious.ts')?.disposition,'code preview only');
    await assert.rejects(readFile(f.root+'/SENTINEL'));
    const before=await readFile(f.root+'/h/systemprompt.md');
    p.content.instructions='CLIENT_FORGERY';
    assert.equal(service.take(p.id,'1',2,false).content.instructions,'IMPORTED_INSTRUCTIONS');
    assert.throws(()=>service.take(p.id,'2',2,false));assert.throws(()=>service.take(p.id,'1',3,false));
    assert.deepEqual(await readFile(f.root+'/h/systemprompt.md'),before);
    const q=await service.preview('1',2,emptyContent('current'),0,'h',{root:0,path:'h'});
    assert.match(q.reference,/Base harness snapshot/);assert.equal(q.before,q.after);assert.equal(q.origin.referenceDigest,q.digest);
  }finally{await f.close();}
});
test('root selection, traversal, symlinks, linked files, invalid text and excessive file size refuse',async()=>{
  const f=await fixture();try{
    for(const p of ['../h','/etc','h/../h','h//skills','h\\skills','h/'])await assert.rejects(snapshotHarness(f.root,p));
    await assert.rejects(createImporter([]).preview('1',1,emptyContent(),0,'h'));
    await symlink(f.root+'/h',f.root+'/alias');await assert.rejects(snapshotHarness(f.root,'alias'));
    await symlink('/etc/passwd',f.root+'/h/leak');await assert.rejects(snapshotHarness(f.root,'h'));await rm(f.root+'/h/leak');
    await link(f.root+'/h/systemprompt.md',f.root+'/outside-hardlink');await assert.rejects(snapshotHarness(f.root,'h'));await rm(f.root+'/outside-hardlink');
    await writeFile(f.root+'/h/systemprompt.md',Buffer.from([255]));await assert.rejects(snapshotHarness(f.root,'h'));
    await writeFile(f.root+'/h/systemprompt.md','x'.repeat(262145));await assert.rejects(snapshotHarness(f.root,'h'));
    assert.throws(()=>importRoots('["/"]'));assert.throws(()=>importRoots('{"root":"/tmp"}'));
  }finally{await f.close();}
});
test('inventory entry and depth budgets refuse without partial import',async()=>{
  const f=await fixture();try{
    for(let i=0;i<257;i++)await writeFile(`${f.root}/h/extra-${i}`,'x');
    await assert.rejects(snapshotHarness(f.root,'h'),/256 entries/);
    await mkdir(f.root+'/deep/a/b/c/d/e/f/g',{recursive:true});
    await assert.rejects(snapshotHarness(f.root,'deep'),/deep/);
  }finally{await f.close();}
});
test('snapshot rejects a changed file and a replaced parent before adopting any data',async()=>{
  const f=await fixture();try{
    await assert.rejects(snapshotHarness(f.root,'h',()=>writeFile(f.root+'/h/systemprompt.md','CHANGED')),/changed/);
    await assert.rejects(snapshotHarness(f.root,'h',async()=>{await rename(f.root+'/h',f.root+'/old');await symlink(f.root+'/old',f.root+'/h');}));
    assert.equal(await readFile(f.root+'/old/systemprompt.md','utf8'),'CHANGED');await assert.rejects(readFile(f.root+'/SENTINEL'));
  }finally{await f.close();}
});
test('oversized preview needs explicit opt-in and malformed/duplicate skills refuse',async()=>{
  const f=await fixture();try{
    await writeFile(f.root+'/h/systemprompt.md','x'.repeat(6500));const s=createImporter([f.root]),p=await s.preview('1',1,emptyContent(),0,'h');
    assert.throws(()=>s.take(p.id,'1',1,false),/6,000/);assert.equal(s.take(p.id,'1',1,true).content.allowLarge,true);
    await mkdir(f.root+'/h/skills/duplicate');await writeFile(f.root+'/h/skills/duplicate/SKILL.md',await readFile(f.root+'/h/skills/careful/SKILL.md'));
    await assert.rejects(snapshotHarness(f.root,'h'),/Duplicate/);
  }finally{await f.close();}
});
test('empty or whitespace-only advisory, skill and extension files are omitted, never empty prompt headings',async()=>{
  const f=await fixture();try{
    await writeFile(f.root+'/h/systemprompt.md',' \n');await writeFile(f.root+'/h/guardrails.md','');await writeFile(f.root+'/h/tool_descriptions/tool_guidance.md',' \n\t');
    await writeFile(f.root+'/h/skills/careful/SKILL.md','  ');await writeFile(f.root+'/h/extensions/malicious.ts','');
    const p=await createImporter([f.root]).preview('1',1,emptyContent(),0,'h');
    for(const name of ['systemprompt.md','guardrails.md','tool_descriptions/tool_guidance.md','skills/careful/SKILL.md','extensions/malicious.ts'])assert.equal(p.files.find(f=>f.path===name)?.disposition,'omitted (empty)');
    assert.equal(p.content.advisory.length,1);assert.equal(p.content.skills.length,0);assert.equal(p.extensions.length,0);
    assert.doesNotMatch(p.after,/guardrails.md|tool_guidance.md|<instructions>/);
  }finally{await f.close();}
});
