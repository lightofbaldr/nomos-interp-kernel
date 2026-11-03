import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const root=new URL('../',import.meta.url).pathname;
const git=(...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8',maxBuffer:20_000_000}).trim();
const expected='Light of Baldr|oss@lightofbaldr.dev|Light of Baldr|oss@lightofbaldr.dev';
assert.equal(git('config','--local','user.name'),'Light of Baldr');
assert.equal(git('config','--local','user.email'),'oss@lightofbaldr.dev');
assert.equal(git('remote','get-url','origin'),'https://github.com/lightofbaldr/glimmer-lab.git');
const log=git('log','--all','--format=%an|%ae|%cn|%ce').split('\n');
assert.ok(log.length>0);for(const entry of log)assert.equal(entry,expected);
assert.equal(git('rev-list','--all','--max-parents=0').split('\n').length,1,'Only the new import root');
assert.equal(git('rev-list','--all','--merges'),'','No imported or review-branch merges');
const files=git('ls-files','-z').split('\0').filter(Boolean);
const forbidden=/(^|\/)(?:node_modules|build|dist|\.svelte-kit|preview|evidence|\.env(?:\.[^/]*)?)(?:\/|$)|\.(?:npz|npy|safetensors|so)$/;
assert.ok(forbidden.test('static/data/x.npz'));
assert.ok(forbidden.test('dist/index.html'));
assert.ok(!forbidden.test('vendor/nomos-svelte-magic-0.1.0.tgz'));
for(const f of files){
  assert.ok(!forbidden.test(f),`Forbidden artifact: ${f}`);
  assert.ok(!/^static\/data\//.test(f),`Capture data: ${f}`);
  const size=Number(git('cat-file','-s',`:${f}`));assert.ok(size<5_000_000,`Unexpected large tracked file: ${f}`);
  if(f.endsWith('.json')&&!f.includes('lock')){
    const b=git('show',`:${f}`);assert.ok(!/"schema"\s*:\s*"nomos\.steerdiff\.(?:ui|compare)\//.test(b),`Capture document: ${f}`);
  }
}
console.log('RELEASE BOUNDARY VERIFIED',log.length,'commits;',files.length,'tracked files');
