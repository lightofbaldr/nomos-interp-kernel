import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFile,realpath} from 'node:fs/promises';
import {parseEnv} from 'node:util';
import {createServer} from 'node:net';
import {once} from 'node:events';
const root=await realpath(new URL('../',import.meta.url).pathname);
const session='nomos-muse-preview',marker=spawnSync('tmux',['show-options','-v','-t',session,'@nomos-muse-project'],{encoding:'utf8'});
const stopped=marker.status!==0;
if(stopped)assert.ok(process.argv.includes('--start-if-stopped'),'No managed preview. Use --start-if-stopped to start on a vacant port.');
else assert.equal(marker.stdout.trim(),root,'Refuse to restart an unmanaged preview');
if(!stopped){
const info=spawnSync('tmux',['list-panes','-t',session,'-F','#{pane_pid}|#{pane_current_path}|#{pane_current_command}'],{encoding:'utf8'});
assert.equal(info.status,0);const lines=info.stdout.trim().split('\n');assert.equal(lines.length,1,'Only the dedicated single-pane preview is owned');
const [pid,cwd,command]=lines[0].split('|');assert.equal(cwd,root);assert.equal(command,'node');
assert.equal(await realpath(`/proc/${pid}/cwd`),root);
const args=(await readFile(`/proc/${pid}/cmdline`,'utf8')).split('\0');assert.ok(args.includes('build/index.js'),'The managed process must be this project server');
const environment=Object.fromEntries((await readFile(`/proc/${pid}/environ`,'utf8')).split('\0').filter(s=>/^(HOST|PORT|ORIGIN)=/.test(s)).map(s=>{const i=s.indexOf('=');return[s.slice(0,i),s.slice(i+1)];}));
assert.ok(environment.HOST==='127.0.0.1'&&environment.PORT==='5226'&&environment.ORIGIN==='http://127.0.0.1:5226','Refuse a differently configured/public server');
}
const quote=s=>`'${s.replaceAll("'","'\\''")}'`;
let local={};try{local=parseEnv(await readFile(root+'/.env','utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
const upstream=process.env.NOMOS_API_BASE_URL??local.NOMOS_API_BASE_URL??'';
if(upstream){const u=new URL(upstream);assert.ok(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash&&['','/'].includes(u.pathname),'Refuse invalid kernel configuration');}
const launch=`env HOST=127.0.0.1 PORT=5226 ORIGIN=http://127.0.0.1:5226 NOMOS_API_BASE_URL=${quote(upstream)} ${quote(process.execPath)} build/index.js`;
let changed;
if(stopped){
  // A missing session is not authority to kill a listener. Prove the port is
  // vacant before creating this dedicated session; never touch other services.
  const probe=createServer();probe.listen(5226,'127.0.0.1');await once(probe,'listening');await new Promise(resolve=>probe.close(resolve));
  changed=spawnSync('tmux',['new-session','-d','-s',session,'-c',root,launch],{encoding:'utf8'});
  assert.equal(changed.status,0,changed.stderr);
  const tagged=spawnSync('tmux',['set-option','-t',session,'@nomos-muse-project',root],{encoding:'utf8'});assert.equal(tagged.status,0,tagged.stderr);
}else changed=spawnSync('tmux',['respawn-pane','-k','-t',`${session}:0.0`,'-c',root,launch],{encoding:'utf8'});
assert.equal(changed.status,0,changed.stderr);
let ready=false;for(let n=0;n<100;n++){try{if((await fetch('http://127.0.0.1:5226/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
assert.ok(ready,'New preview did not become ready; do not trust an old listener');
const own=spawnSync('tmux',['list-panes','-t',session,'-F','#{pane_current_path}|#{pane_current_command}'],{encoding:'utf8'});assert.equal(own.status,0);assert.equal(own.stdout.trim(),root+'|node','The ready listener must still have its owned process');
console.log('MUSE PREVIEW REFRESHED: only the owned loopback tmux server.');
