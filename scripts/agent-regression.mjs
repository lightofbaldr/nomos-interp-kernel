// All application tests use synthetic providers and isolated databases. Never
// invoke a live-e2e/kernel client or a paid annotator from this runner.
import {spawnSync} from 'node:child_process';
import {readdirSync,mkdirSync,appendFileSync,existsSync} from 'node:fs';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
mkdirSync('preview/checks',{recursive:true});
const log=`preview/checks/agent-regression-${new Date().toISOString().replace(/[:.]/g,'-')}.log`;
const unit=readdirSync('tests',{recursive:true}).filter(p=>/\.test\.(ts|mjs)$/.test(p)).map(p=>'tests/'+p).sort();
const checks=[['scripts/verify.mjs'],['scripts/check-dependencies.mjs'],
  ['--test','--test-concurrency=1','--test-reporter=tap',...unit],
  ['scripts/agent-workspace-tests.mjs'],['scripts/filetool-tests.mjs','sandbox'],['scripts/mojo-tests.mjs','sandbox'],
  ...(existsSync('scripts/shell-tests.mjs')?[['scripts/shell-tests.mjs']]:[]),['scripts/purge-tests.mjs'],['scripts/steering-tests.mjs'],
  ...['transport','lab-integration','browser','ui','contrast','profile-browser','filetool-browser','mojo-browser','generation-browser','live-browser','controls-browser','meta-browser','workspace-browser','inspection-browser','step2-browser','purge-browser','workbench-recorded-browser','workbench-recorded-followups-browser','reconnect-browser','agent-browser','shell-browser','start-check'].map(p=>[`scripts/${p}.mjs`])];
for(const args of checks){
  const result=spawnSync(process.execPath,args,{encoding:'utf8',maxBuffer:24*1024*1024,timeout:600000});
  const output=(result.stdout??'')+(result.stderr??'')+(result.error?String(result.error):'');
  appendFileSync(log,JSON.stringify({args,status:result.status,signal:result.signal})+'\n'+output+'\n');process.stdout.write(output);
  if(result.status!==0){console.error('Agent regression stopped:',JSON.stringify(args),log);process.exit(result.status??1);}
}
console.log('Evidence:',log);console.log('AGENT MODE REGRESSION VERIFIED');
