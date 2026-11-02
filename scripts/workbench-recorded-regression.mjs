import {spawnSync} from 'node:child_process';
import {mkdirSync,appendFileSync} from 'node:fs';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
mkdirSync('preview/checks',{recursive:true});
const log=`preview/checks/workbench-recorded-regression-${new Date().toISOString().replace(/[:.]/g,'-')}.log`;
for(const args of [
  ['--test','tests/origin.test.ts','tests/workspace/model.test.ts','tests/workspace/step2.test.ts','tests/inspection/model.test.ts','tests/generation/retry.test.ts'],
  ['scripts/profile-tests.mjs','runtime'],['scripts/profile-browser.mjs'],['scripts/purge-tests.mjs'],['scripts/purge-browser.mjs']
]){
  const r=spawnSync(process.execPath,args,{encoding:'utf8',maxBuffer:16*1024*1024});
  const output=(r.stdout??'')+(r.stderr??'')+(r.error?String(r.error):'');
  appendFileSync(log,JSON.stringify({args,status:r.status,signal:r.signal})+'\n'+output+'\n');process.stdout.write(output);
  if(r.status!==0){console.error(`Regression stopped: ${JSON.stringify(args)}; status=${r.status}, signal=${r.signal}; ${log}`);process.exit(r.status??1);}
}
console.log(`Evidence: ${log}`);
console.log('RECORDED WORKBENCH REGRESSION VERIFIED');
