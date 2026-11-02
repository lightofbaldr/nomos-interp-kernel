import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const env={...process.env};delete env.CLAUDECODE;
for(const task of ['check','build']){
  const r=spawnSync('npm',['run',task],{encoding:'utf8',env,maxBuffer:16*1024*1024});process.stdout.write((r.stdout??'')+(r.stderr??''));
  if(r.status!==0||(task==='check'&&!/0 errors and 0 warnings/.test(r.stdout??'')))process.exit(r.status||1);
}
console.log('RECORDED WORKBENCH BUILD VERIFIED');
