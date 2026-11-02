import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const result=spawnSync(process.execPath,['--test','--test-concurrency=1','--test-reporter=tap','tests/agent/controls.test.ts','tests/agent/readout-continuity.test.ts','tests/agent/budget.test.ts','tests/shell/integration.test.ts'],{stdio:'inherit'});
if(result.status!==0)process.exit(result.status??1);
console.log('AGENT MODE CONTROLS VERIFIED');
