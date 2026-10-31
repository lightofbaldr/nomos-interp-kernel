import {spawnSync} from 'node:child_process';
for(const args of [['scripts/filetool-tests.mjs','integration'],['scripts/filetool-tests.mjs','sandbox'],['scripts/filetool-regression.mjs'],['scripts/mojo-browser.mjs']]){
  const r=spawnSync(process.execPath,args,{stdio:'inherit'});if(r.status!==0)process.exit(r.status??1);
}
console.log('MOJO REGRESSIONS PASS');
