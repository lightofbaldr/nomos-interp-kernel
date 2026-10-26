import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync, spawnSync} from 'node:child_process';
import path from 'node:path';
const root = new URL('../', import.meta.url).pathname;
const git = (...args) => execFileSync('git', args, {cwd:root,env:{...process.env,GIT_OPTIONAL_LOCKS:'0'},maxBuffer:20_000_000});
const sha = b => createHash('sha256').update(b).digest('hex');
const manifest = JSON.parse(await readFile(path.join(root,'docs/import/snapshot.json')));
const hasHead = spawnSync('git',['rev-parse','--verify','HEAD'],{cwd:root,stdio:'ignore'}).status===0;
const roots = hasHead ? git('rev-list','--max-parents=0','HEAD').toString().trim().split('\n') : [];
assert.ok(roots.length<=1,'The import must have no inherited ancestry');
assert.equal(new Set(manifest.files.map(f=>f.path)).size,manifest.files.length);
for(const f of manifest.files){
  const bytes = hasHead ? git('show',`${roots[0]}:${f.path}`) : await readFile(path.join(root,f.path));
  assert.equal(sha(bytes),f.sha256,`Imported bytes changed: ${f.path}`);
  assert.equal(bytes.length,f.bytes);
  if(process.argv.includes('--source')) assert.equal(sha(await readFile(path.join(manifest.source_repo,manifest.source_path,f.path))),f.sha256,`Source changed since import: ${f.path}`);
}
if(process.argv.includes('--source')){
  const sourceGit=(...args)=>execFileSync('git',args,{cwd:manifest.source_repo,env:{...process.env,GIT_OPTIONAL_LOCKS:'0'},encoding:'utf8'});
  assert.equal(sourceGit('rev-parse','HEAD').trim(),manifest.base_commit);
  assert.equal(sourceGit('remote','-v'),manifest.source_remote);
  assert.equal(sourceGit('status','--porcelain=v1','--untracked-files=all','--',manifest.source_path),manifest.source_status);
}
const counts={};for(const f of manifest.files)counts[f.source_state]=(counts[f.source_state]??0)+1;
console.log('SNAPSHOT VERIFIED',manifest.files.length,counts);
