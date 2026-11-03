import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
// Offline HTTP producers only. Never run test:kernel / live-e2e here.
const commands=[['--test','--test-reporter=tap','tests/origin.test.ts','tests/live/model.test.ts','tests/live/server.test.ts','tests/meta/client.test.ts','tests/meta/summary.test.ts','tests/meta/service.test.ts'],
  ['scripts/workspace-tests.mjs'],['scripts/workspace-browser.mjs'],['scripts/meta-browser.mjs'],['scripts/start-check.mjs']];
for(const args of commands){const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);}
console.log('INSPECTION REGRESSION VERIFIED');
