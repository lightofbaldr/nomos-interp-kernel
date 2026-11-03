import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
// Build first. Synthetic providers/isolated DBs and read-only recordings only;
// never the live lab, a live kernel client, a paid provider, or a restart.
const checks=[['scripts/verify.mjs'],['--test','tests/live/fixture.test.ts','tests/files/fixture.test.ts'],['scripts/live-regression.mjs'],
  ...['profile-browser','filetool-browser','mojo-browser','generation-browser','live-browser','controls-browser','meta-browser','workspace-browser','inspection-browser','step2-browser','purge-browser','workbench-recorded-browser','workbench-recorded-followups-browser','reconnect-browser'].map(name=>[`scripts/${name}.mjs`])];
for(const args of checks){const result=spawnSync(process.execPath,args,{stdio:'inherit',timeout:600000});if(result.status!==0)process.exit(result.status??1);}
console.log('BRIDGE RECOVERY REGRESSION VERIFIED');
