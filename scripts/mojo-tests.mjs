import {spawnSync} from 'node:child_process';
const mode=process.argv[2];
if(!['integration','sandbox'].includes(mode))throw Error('Choose integration or sandbox.');
const r=mode==='sandbox'?spawnSync('/usr/bin/python3',['-I','-B','tests/mojo/sandbox_test.py'],{stdio:'inherit'}):spawnSync(process.execPath,['--test','tests/mojo/integration.test.ts'],{stdio:'inherit'});
if(r.status!==0)process.exit(r.status??1);
console.log(mode==='sandbox'?'MOJO SANDBOX PASS':'MOJO INTEGRATION PASS');
