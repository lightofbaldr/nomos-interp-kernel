import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const probe=new URL('../tests/start-listen-probe.mjs',import.meta.url).href;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function vacantPort(){
  const s=createServer();s.listen(0,'127.0.0.1');await once(s,'listening');
  const port=s.address().port;await new Promise(resolve=>s.close(resolve));return port;
}
function listening(port){
  // Independent Linux socket-table check, not just the adapter's startup log.
  return execFileSync('ss',['-H','-ltn',`sport = :${port}`],{encoding:'utf8'}).trim();
}
async function checkStart(label,host,expected,mode){
  const port=await vacantPort(),env={...process.env};
  for(const key of ['HOST','SOCKET_PATH','LISTEN_PID','LISTEN_FDS','LISTEN_FDNAMES'])delete env[key];
  Object.assign(env,{
    PORT:String(port),ORIGIN:`http://127.0.0.1:${port}`,SHUTDOWN_TIMEOUT:'1',
    NOMOS_API_BASE_URL:'',GLIMMER_STEERDIFF_DATA_DIR:root+'evidence/absent-start-check-data',
    NODE_OPTIONS:`--import=${probe}`,LAB_START_EXPECT_HOST:expected,LAB_START_CHECK_MODE:mode
  });
  if(host!==undefined)env.HOST=host;
  const child=spawn('npm',['run','--silent','start'],{cwd:root,env,detached:true,stdio:['ignore','pipe','pipe']});
  let stdout='',stderr='',failure;
  child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);child.on('error',e=>failure=e);
  const done=new Promise(resolve=>child.once('close',resolve));
  try{
    const marker=mode==='listen'?'START_SOCKET ':'START_INTENT ';
    const deadline=Date.now()+10_000;
    while(!stdout.includes(marker)){
      if(failure)throw failure;
      if(child.exitCode!==null || child.signalCode!==null)throw Error(`${label}: start failed\n${stdout}\n${stderr}`);
      if(Date.now()>deadline)throw Error(`${label}: startup timed out\n${stdout}\n${stderr}`);
      await delay(50);
    }
    const result=JSON.parse(stdout.split('\n').find(line=>line.startsWith(marker)).slice(marker.length));
    if(mode==='listen'){
      assert.deepEqual(result,{address:'127.0.0.1',family:'IPv4',port});
      const lines=listening(port).split('\n').filter(Boolean);
      assert.equal(lines.length,1,'Exactly one TCP listener for this start');
      assert.equal(lines[0].trim().split(/\s+/)[3],`127.0.0.1:${port}`,'Kernel socket table is loopback-only');
      const response=await fetch(`http://127.0.0.1:${port}/steering-diff`,{signal:AbortSignal.timeout(3000)});
      assert.equal(response.status,200);assert.ok((await response.text()).includes('Workspace difference'));
      console.log(`${label}: real npm start listener 127.0.0.1:${port}; ss and HTTP verified`);
    }else{
      await done;assert.equal(child.exitCode,0,stderr);assert.deepEqual(result,{host:expected,port});
      assert.equal(listening(port),'','Inspect-only override must not open a listener');
      console.log(`${label}: explicit ${expected} reaches adapter; no socket opened`);
    }
  }finally{
    // A dedicated process group contains only this test's npm + shell + server.
    if(child.pid){
      try{process.kill(-child.pid,'SIGTERM');}catch(e){if(e.code!=='ESRCH')throw e;}
      await Promise.race([done,delay(1500)]);
      try{process.kill(-child.pid,'SIGKILL');}catch(e){if(e.code!=='ESRCH')throw e;}
      await done;
    }
  }
  assert.equal(listening(port),'','Test listener is cleaned up');
}

assert.equal(process.platform,'linux','This listener regression requires Linux ss');
await checkStart('HOST unset',undefined,'127.0.0.1','listen');
await checkStart('HOST empty','','127.0.0.1','listen');
await checkStart('Explicit IPv4 opt-in','0.0.0.0','0.0.0.0','inspect');
await checkStart('Explicit IPv6 opt-in','::','::','inspect');
console.log('LOOPBACK START VERIFIED: unset and empty defaults; explicit wider opt-ins; all listeners cleaned up');
