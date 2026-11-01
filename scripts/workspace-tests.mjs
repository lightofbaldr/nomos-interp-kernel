import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const r=spawnSync(process.execPath,['--test','tests/workspace/model.test.ts','tests/workspace/history.test.ts'],{stdio:'inherit'});
if(r.status!==0)process.exit(r.status??1);
console.log('WORKSPACE MODEL VERIFIED');
