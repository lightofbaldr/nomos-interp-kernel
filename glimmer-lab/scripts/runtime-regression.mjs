import {spawnSync} from 'node:child_process';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
console.log('Regression runtime:',process.version,'Undici',process.versions.undici,process.execPath);
for(const args of [
  ['--test','tests/runtime.test.mjs'],
  ['scripts/check-dependencies.mjs'],
  ['scripts/mojo-tests.mjs','integration'],
  ['scripts/mojo-tests.mjs','sandbox'],
  ['scripts/mojo-regression.mjs'],
  ['--test','--test-reporter=tap','tests/generation/runtime.test.ts','tests/generation/retry.test.ts'],
  ['scripts/generation-browser.mjs']
]){
  const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);
}
console.log('RUNTIME REGRESSIONS PASS');
