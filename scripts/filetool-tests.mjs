import {spawnSync} from 'node:child_process';
const mode=process.argv[2];
if(!['sandbox','integration'].includes(mode))throw Error('Choose sandbox or integration.');
const r=mode==='sandbox'?spawnSync('/usr/bin/python3',['-I','-B','tests/files/sandbox_test.py'],{stdio:'inherit'}):spawnSync(process.execPath,['--test','tests/files/integration.test.ts'],{stdio:'inherit'});
if(r.status!==0)process.exit(r.status??1);
console.log(mode==='sandbox'?'FILE TOOL SANDBOX PASS':'FILE TOOL INTEGRATION PASS');
