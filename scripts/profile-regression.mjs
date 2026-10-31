import {spawnSync} from 'node:child_process';
// Fresh production build, existing fixtures/browsers only. Never a live kernel or paid model.
const checks=[
  ['scripts/verify.mjs'],
  ['--test','tests/live/alerts.test.ts','tests/live/model.test.ts','tests/live/server.test.ts','tests/meta/client.test.ts','tests/meta/service.test.ts','tests/meta/summary.test.ts'],
  ['scripts/live-regression.mjs'],
  ['scripts/live-browser.mjs'],
  ['scripts/meta-browser.mjs']
];
for(const args of checks){const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);}
console.log('PROFILE REGRESSIONS PASS');
