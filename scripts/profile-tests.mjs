import {spawnSync} from 'node:child_process';
const mode=process.argv[2];
if(!['runtime','import'].includes(mode))throw Error('Choose runtime or import tests.');
const r=spawnSync(process.execPath,['--test',`tests/profile/${mode}.test.ts`],{stdio:'inherit'});
if(r.status!==0)process.exit(r.status??1);
console.log(mode==='runtime'?'PROFILE INTEGRATION PASS':'PROFILE IMPORT PASS');
