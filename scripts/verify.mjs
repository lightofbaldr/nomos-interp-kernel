import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readdir,readFile,mkdir,writeFile} from 'node:fs/promises';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const root=new URL('../',import.meta.url).pathname;
async function walk(dir){return (await Promise.all((await readdir(dir,{withFileTypes:true})).map(e=>e.isDirectory()?walk(`${dir}/${e.name}`):`${dir}/${e.name}`))).flat();}
for(const file of await walk(root+'src')){
  if(!file.endsWith('.svelte'))continue;
  assert.ok(!/export let |on:click=|<slot(?:\s|>)|<svelte:component/.test(await readFile(file,'utf8')),'Use native Svelte 5 component composition');
}
for(const task of ['check','build']){
  const r=spawnSync('npm',['run',task],{cwd:root,encoding:'utf8',maxBuffer:15_000_000});
  assert.equal(r.status,0,r.stdout+r.stderr);if(task==='check')assert.match(r.stdout,/0 errors and 0 warnings/);
}
await mkdir(root+'preview/checks',{recursive:true});
await writeFile(root+'preview/checks/build.json',JSON.stringify({checkedAt:new Date().toISOString(),passed:true}));
console.log('MUSE BUILD PASSED');
