import {spawnSync} from 'node:child_process';
// Builds first; no live endpoint or paid API. Synthetic providers and isolated databases only.
for(const args of [['scripts/profile-regression.mjs'],['scripts/profile-tests.mjs','runtime'],['scripts/profile-tests.mjs','import'],['scripts/profile-browser.mjs'],['scripts/filetool-browser.mjs']]){
  const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);
}
console.log('FILE TOOL REGRESSIONS PASS');
