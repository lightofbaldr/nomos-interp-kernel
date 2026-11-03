import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
// All providers are fixtures; no live inference or paid calls. Runtime regression
// also covers profiles, sandboxed file/Mojo tools, annotations and generation.
for(const args of [
  ['scripts/runtime-regression.mjs'],
  ['scripts/inspection-regression.mjs'],
  ['--test','tests/inspection/model.test.ts','tests/inspection/system.test.ts'],
  ['scripts/workspace-browser.mjs','--recorded']
]){const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);}
console.log('STEP2 REGRESSION VERIFIED');
