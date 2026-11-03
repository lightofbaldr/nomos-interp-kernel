import {spawnSync} from 'node:child_process';
const commands=[
  ['--test','tests/steering/model.test.ts','tests/steering/scenarios.test.ts','tests/steering/sampled.test.ts','tests/steering/data-server.test.ts','tests/steering/responses.test.ts'],
  ['scripts/steering-data-check.mjs'],
  ['scripts/steering-browser.mjs'],
  ['scripts/steering-scenario-browser.mjs'],
  ['scripts/steering-lies-browser.mjs'],
  ['scripts/steering-sampled-browser.mjs'],
  ['scripts/steering-response-browser.mjs']
];
for(const args of commands){const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.error)throw r.error;if(r.status!==0)process.exit(r.status??1);}
console.log('STEERING DIFF VERIFIED');
