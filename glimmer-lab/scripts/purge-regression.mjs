import {spawnSync} from 'node:child_process';
import {mkdirSync,writeFileSync,appendFileSync} from 'node:fs';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
mkdirSync(new URL('../preview/checks/',import.meta.url),{recursive:true});
const log=new URL('../preview/checks/purge-regression.log',import.meta.url);writeFileSync(log,'');
for(const args of [
  ['--test','tests/generation/runtime.test.ts','tests/generation/retry.test.ts','tests/origin.test.ts','tests/live/server.test.ts','tests/meta/service.test.ts'],
  ['scripts/profile-tests.mjs','runtime'],['scripts/filetool-tests.mjs','integration'],['scripts/mojo-tests.mjs','integration'],
  ['scripts/profile-browser.mjs'],['scripts/generation-browser.mjs'],['scripts/workspace-tests.mjs'],['scripts/meta-browser.mjs']
]){
  const r=spawnSync(process.execPath,args,{encoding:'utf8',maxBuffer:16*1024*1024});
  const output=(r.stdout??'')+(r.stderr??'')+(r.error?String(r.error):'');
  appendFileSync(log,JSON.stringify(args)+'\n'+output);process.stdout.write(output);
  if(r.status!==0)process.exit(r.status??1);
}
console.log('PURGE REGRESSION VERIFIED');
