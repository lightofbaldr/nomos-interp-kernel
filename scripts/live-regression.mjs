import {spawnSync} from 'node:child_process';
// Read-only and fixture/browser checks only. No live model request here.
for(const args of [
  ['--test','tests/contract.test.mjs'],
  ['scripts/transport.mjs'],
  ['scripts/steering-tests.mjs'],
  ['scripts/lab-integration.mjs'],
  ['scripts/controls-browser.mjs'],
  ['scripts/browser.mjs'],
  ['scripts/ui.mjs'],
  ['scripts/contrast.mjs'],
  ['scripts/start-check.mjs']
]){const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);}
console.log('LIVE REGRESSIONS VERIFIED');
