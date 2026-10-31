import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawnSync} from 'node:child_process';
import {mkdtemp,readFile,rm,mkdir,symlink,writeFile,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {SUPPORTED_NODE,SUPPORTED_UNDICI,assertSupportedRuntime} from '../scripts/runtime.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));

test('runtime declarations agree with the actual supported Node and npm override',async()=>{
  assertSupportedRuntime();
  assert.equal(process.versions.node,'24.22.1');assert.equal(process.versions.undici,'7.30.1');
  assert.equal(SUPPORTED_NODE,'24.22.1');assert.equal(SUPPORTED_UNDICI,'7.30.1');
  const pkg=JSON.parse(await readFile(root+'package.json','utf8'));
  assert.equal(pkg.engines.node,SUPPORTED_NODE);assert.equal(pkg.overrides.undici,'6.29.1');
  assert.equal((await readFile(root+'.nvmrc','utf8')).trim(),SUPPORTED_NODE);
});

test('older, absent, or unexpected runtime identities fail closed',()=>{
  assert.doesNotThrow(()=>assertSupportedRuntime({node:'24.22.1',undici:'7.30.1'}));
  for(const versions of [{node:'24.18.0',undici:'7.28.0'},{node:'24.22.1',undici:'7.28.0'},
    {node:'24.22.1'},{undici:'7.30.1'},{node:'24.22.2',undici:'7.30.1'}]){
    assert.throws(()=>assertSupportedRuntime(versions),/requires Node 24\.22\.1.*Undici 7\.30\.1/);
  }
});

test('start and preview refuse a mismatched runtime before loading the adapter or touching tmux',async()=>{
  const dir=await mkdtemp(path.join(tmpdir(),'glimmer-runtime-'));
  try{
    const preload=path.join(dir,'wrong-version.mjs');
    await writeFile(preload,`Object.defineProperty(process.versions,'undici',{value:'7.28.0'});\n`);
    for(const entry of ['scripts/start.mjs','scripts/refresh-preview.mjs']){
      const r=spawnSync(process.execPath,['--import',preload,root+entry],{cwd:dir,env:{...process.env,PATH:'/does-not-exist'},encoding:'utf8',timeout:5000});
      assert.notEqual(r.status,0);assert.match(r.stderr,/requires Node 24\.22\.1.*Undici 7\.30\.1/);
      assert.doesNotMatch(r.stdout,/GLIMMER_RUNTIME|REFRESHED|Listening/);
    }
  }finally{await rm(dir,{recursive:true,force:true});}
});

test('launcher selects the pinned installed node despite stale PATH and preserves arguments',async()=>{
  const dir=await mkdtemp(path.join(tmpdir(),'glimmer-launcher-'));
  try{
    const bin=path.join(dir,'nvm/versions/node/v24.22.1/bin'),fake=path.join(dir,'stale');
    await mkdir(bin,{recursive:true});await mkdir(fake);
    await symlink(process.execPath,path.join(bin,'node'));
    await writeFile(path.join(fake,'node'),'#!/bin/sh\nprintf "STALE_NODE_RAN\\n"\nexit 88\n',{mode:0o700});
    // The trusted preload exits before the adapter, while proving the selected interpreter.
    const probe=path.join(dir,'selected.mjs');
    await writeFile(probe,`if(process.argv[1].endsWith('/scripts/start.mjs')){console.log(JSON.stringify({node:process.versions.node,undici:process.versions.undici,args:process.argv.slice(2)}));process.exit(0);}\n`);
    const env={...process.env,PATH:fake+':/usr/bin:/bin',NVM_DIR:path.join(dir,'nvm'),NODE_OPTIONS:'--import='+probe};delete env.GLIMMER_NODE_BIN;
    const r=spawnSync('/bin/sh',[root+'scripts/start.sh','a b','c$d'],{cwd:dir,env,encoding:'utf8',timeout:5000});
    assert.equal(r.status,0,r.stderr);assert.deepEqual(JSON.parse(r.stdout),{node:'24.22.1',undici:'7.30.1',args:['a b','c$d']});
    const bad=spawnSync('/bin/sh',[root+'scripts/start.sh'],{cwd:dir,env:{...env,GLIMMER_NODE_BIN:'/no/such/node'},encoding:'utf8',timeout:5000});
    assert.notEqual(bad.status,0);assert.doesNotMatch(bad.stdout,/STALE_NODE_RAN|24\.22\.1/);
  }finally{await rm(dir,{recursive:true,force:true});}
});

test('dependency inventory refuses vulnerable, missing, and contradictory copies',async()=>{
  const dir=await mkdtemp(path.join(tmpdir(),'glimmer-dependencies-'));
  try{
    await mkdir(dir+'/scripts');await mkdir(dir+'/node_modules/undici',{recursive:true});
    for(const name of ['runtime.mjs','check-dependencies.mjs'])await copyFile(root+'scripts/'+name,dir+'/scripts/'+name);
    for(const fault of ['healthy','override','lock','installed','missing']){
      await writeFile(dir+'/package.json',JSON.stringify({overrides:{undici:fault==='override'?'6.29.0':'6.29.1'}}));
      await writeFile(dir+'/package-lock.json',JSON.stringify({packages:{'':{engines:{node:'24.22.1'}},...(fault==='missing'?{}:{'node_modules/undici':{version:fault==='lock'?'6.29.0':'6.29.1'}})}}));
      await writeFile(dir+'/node_modules/undici/package.json',JSON.stringify({version:fault==='installed'?'6.29.0':'6.29.1'}));
      const r=spawnSync(process.execPath,[dir+'/scripts/check-dependencies.mjs'],{encoding:'utf8',timeout:5000});
      if(fault==='healthy'){assert.equal(r.status,0,r.stderr);assert.match(r.stdout,/UNDICI DEPENDENCIES VERIFIED/);}
      else{assert.notEqual(r.status,0,fault);assert.doesNotMatch(r.stdout,/UNDICI DEPENDENCIES VERIFIED/);}
    }
  }finally{await rm(dir,{recursive:true,force:true});}
});
