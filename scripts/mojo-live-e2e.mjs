// Opt-in client only. Never starts/restarts Nomos; no paid provider is configured.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {homedir} from 'node:os';
import {harness,pause,root} from './harness.mjs';
import {inspect,outcome as checkOutcome} from './mojo-live-result.mjs';

const [mode,destination]=process.argv.slice(2);
assert.ok(['--fixture','--live'].includes(mode)&&destination&&process.argv.length===4,
  'Use --fixture|--live NEW_EVIDENCE_DIR; live requires an explicitly coordinated slot.');
assert.ok(path.isAbsolute(destination),'Evidence directory must be absolute.');
const prefix=process.env.GLIMMER_MOJO_PREFIX;
assert.ok(prefix&&path.isAbsolute(prefix),'Set the operator-approved GLIMMER_MOJO_PREFIX.');
const live=mode==='--live',base='http://127.0.0.1:8091',marker='glimmer-mojo-live-ok';
const source=`def main():\n    print("${marker}")\n`;
const prompt=`Use the sandboxed tools, not a prose-only answer. With write, create main.mojo containing exactly:\n${source}\nThen invoke mojo on main.mojo once. Report the actual compile/run exit codes and stdout. Do not claim a run unless its tool result exists. This is only a tool-path smoke check.`;
const sha=b=>createHash('sha256').update(b).digest('hex');
const save=(name,value)=>writeFile(path.join(destination,name),JSON.stringify(value,null,2)+'\n',{flag:'wx',mode:0o600});
const connections=()=>execFileSync('ss',['-Htnp','state','established','( sport = :8091 or dport = :8091 )'],{encoding:'utf8'}).trim();
const listener=()=>execFileSync('ss',['-Hltpn','sport = :8091'],{encoding:'utf8'}).trim();
const noClients=()=>assert.equal(connections(),'','Another client is using :8091; no submission allowed.');
function ownedConnections(pid){
  const rows=connections().split('\n').filter(Boolean).map(line=>({line,fields:line.trim().split(/\s+/)}));
  const clients=rows.filter(r=>r.fields[3]==='127.0.0.1:8091');
  assert.ok(clients.every(r=>r.line.includes(`pid=${pid},`)),'A different local client reached :8091');
  for(const row of rows.filter(r=>r.fields[2]==='127.0.0.1:8091')){
    assert.ok(clients.some(c=>c.fields[2]===row.fields[3]),'A foreign client reached :8091');
  }
  assert.equal(rows.length,clients.length*2,'Unexpected non-loopback serve connection');
  return {at:new Date().toISOString(),clientConnections:clients.length};
}
async function health(){
  const r=await fetch(base+'/health',{headers:{Connection:'close'},redirect:'error',signal:AbortSignal.timeout(5000)});
  assert.equal(r.status,200);const h=await r.json();assert.equal(h.ready,true);assert.equal(h.readout?.supported,true);return h;
}
async function snapshot(page,id){
  return page.evaluate(async id=>{
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);
    try{
      const r=await fetch(`/api/pi?conversation=${id}`,{signal:abort.signal});if(!r.ok)throw Error('Snapshot unavailable');
      const reader=r.body.getReader(),decoder=new TextDecoder();let text='';
      for(;;){const part=await reader.read();if(part.done)throw Error('Snapshot ended early');text+=decoder.decode(part.value,{stream:true});
        const at=text.indexOf('\n\n');if(at>=0){const value=JSON.parse(text.slice(0,at).replace(/^data: /,''));await reader.cancel();return value;}
        if(text.length>8_000_000)throw Error('Snapshot exceeded this smoke bound');}
    }finally{clearTimeout(timer);abort.abort();}
  },id);
}
const outcome=(s,bytes,profile)=>checkOutcome(s,bytes,profile,source,marker);

await mkdir(destination,{mode:0o700}); // Exclusive: no adoption or overwrite of an earlier run.
let f,h,page,id,serverPid,submitted=false,settled=false,result=null,failure=null,before=null,beforeListener=null,after=null;
const connectionSamples=[];
try{
  if(live){noClients();beforeListener=listener();assert.ok(beforeListener);before=await health();await pause(150);noClients();}
  else{f=await(await import('../tests/files/fixture.ts')).fileKernel();f.plan([{name:'write',args:{path:'main.mojo',content:source}},{name:'mojo',args:{path:'main.mojo'}},null]);}
  const tokenizer=live?path.join(homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json'):f.tokenizerPath;
  const pin=live?'c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d':f.tokenizerPin;
  assert.equal(sha(await readFile(tokenizer)),pin);
  const db=path.join(destination,'session.sqlite');
  await save('launch.json',{mode,prompt,started:new Date().toISOString(),node:process.versions.node,undici:process.versions.undici,
    commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),script:sha(await readFile(new URL(import.meta.url))),
    checker:sha(await readFile(new URL('./mojo-live-result.mjs',import.meta.url))),
    compilerPrefix:prefix,tokenizer:pin,before,beforeListener});
  h=await harness(false,{NOMOS_API_BASE_URL:live?base:f.base,GLIMMER_SESSION_DB:db,GLIMMER_MOJO_PREFIX:prefix,
    GLIMMER_META_ENV_FILE:path.join(destination,'no-meta-key.env'),NOMOS_TOKENIZER_JSON:tokenizer,NOMOS_TOKENIZER_SHA256:pin,
    ...(f?{NOMOS_READOUT_REGISTRY:f.registry}:{})});
  if(live){const bound=execFileSync('ss',['-Hltpn',`sport = :${new URL(h.base).port}`],{encoding:'utf8'});serverPid=Number(bound.match(/pid=(\d+),/)?.[1]);assert.ok(serverPid);}
  page=await h.browser.newPage({viewport:{width:1500,height:1100}});h.observe(page);page.setDefaultTimeout(30000);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();
  const capabilities=await page.evaluate(async()=>(await(await fetch('/api/pi')).json()));
  assert.equal(capabilities.annotator.available,false,'Paid annotator must be unavailable for this run');
  id=await page.getByLabel('Pi conversation').inputValue();
  await page.getByRole('tab',{name:'Profile',exact:true}).click();await page.getByLabel('Enable tool mojo',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Enable tool mojo',{exact:true}).isChecked(),false);
  await page.getByRole('button',{name:'Check Mojo toolchain',exact:true}).click();
  await page.getByTestId('mojo-availability').filter({hasText:'Mojo available'}).waitFor();
  for(const name of ['write','mojo'])await page.getByLabel(`Enable tool ${name}`,{exact:true}).check();
  await page.getByRole('button',{name:'Save profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  const saved=await page.evaluate(async id=>(await(await fetch(`/api/pi/profile?conversation=${id}`)).json()).profile,id);
  assert.deepEqual(saved.active.content.tools,['write','mojo']);await save('profile.json',saved);
  if(live){await pause(150);noClients();}
  await page.getByLabel('Message Muse',{exact:true}).fill(prompt);submitted=true;
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  const deadline=Date.now()+300000;let s;
  for(;;){
    await pause(1000);if(live)connectionSamples.push(ownedConnections(serverPid));s=await snapshot(page,id);const v=inspect(s);
    if(v.assistants.length&&!v.pi.run&&!v.pi.generation&&!v.pi.inbox.length){settled=true;break;}
    if(Date.now()>deadline||v.calls.length>4)throw Error('Single-submission smoke bound reached; abort without retry');
  }
  await save('snapshot.json',s);
  const bytes=await readFile(db+`.workspaces/workspaces/${id}/main.mojo`);await writeFile(path.join(destination,'main.mojo'),bytes,{flag:'wx',mode:0o600});
  result=outcome(s,bytes,saved);
  const tool=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/mojo Completed/})});
  await tool.getByRole('button').click();assert.equal((await tool.getByLabel('Mojo run stdout',{exact:true}).innerText()).trim(),marker);
  await page.screenshot({path:path.join(destination,'ui.png'),fullPage:true});
  // Reconnect/reload the UI; the verified result must come back from the saved Pi record.
  await page.reload();await page.getByText('Pi connected',{exact:true}).waitFor();
  const reloaded=await snapshot(page,id);const restored=outcome(reloaded,bytes,saved);assert.deepEqual(restored,result);
  await save('reload.json',reloaded);assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  if(f)assert.equal(f.requests.length,3);
}catch(e){failure={message:String(e),stack:e?.stack};
  if(page&&id){try{await save('failure-snapshot.json',await snapshot(page,id));}catch{}try{await page.screenshot({path:path.join(destination,'failure.png'),fullPage:true});}catch{}}
}finally{
  if(submitted&&!settled&&page&&id){try{await page.evaluate(async id=>{const r=await fetch('/api/pi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'abort',conversation:id}),signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('Abort refused');},id);}catch(e){failure??={message:String(e)};}}
  try{await h?.close();}catch(e){failure??={message:String(e)};}
  try{await f?.close();}catch(e){failure??={message:String(e)};}
  if(live){try{await pause(1000);noClients();after=await health();await pause(200);noClients();assert.equal(listener(),beforeListener,'Serve listener changed');}catch(e){failure??={message:String(e)};}}
  await save('result.json',{status:failure?'failed':'pass',mode,submitted,settled,result,failure,after,serverPid,connectionSamples,completed:new Date().toISOString()});
}
console.log('Evidence:',destination);
if(failure){console.error(failure.message);process.exitCode=1;}else console.log(live?'LIVE MOJO E2E VERIFIED':'FIXTURE MOJO E2E VERIFIED');
