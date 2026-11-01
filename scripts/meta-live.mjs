// Explicit, paid acceptance run. Never invoked by verify or any test command.
// Each fresh Pi turn is generated once; each Meta annotation is requested once.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import path from 'node:path';
import {harness,pause,root} from './harness.mjs';
const [accept,out]=process.argv.slice(2);
assert.ok(['--accept-up-to-three-paid-calls','--accept-one-paid-afraid-check'].includes(accept));assert.ok(out&&path.isAbsolute(out),'Provide a fresh absolute evidence directory.');
const base='http://127.0.0.1:8091',home=homedir(),hash=x=>createHash('sha256').update(x).digest('hex');
const noClients=()=>assert.equal(execFileSync('ss',['-Htn','state','established','( sport = :8091 )'],{encoding:'utf8'}).trim(),'','Another client is using :8091');
const health=async()=>{const h=await(await fetch(base+'/health',{headers:{Connection:'close'},signal:AbortSignal.timeout(5000)})).json();assert.equal(h.ready,true);assert.equal(h.readout?.supported,true);return h;};
noClients();const before=await health();
const tokenizer=path.join(home,'nomos_data/muse-glimmer-tokenizer/tokenizer.json'),tokenPin='c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d';
assert.equal(hash(await readFile(tokenizer)),tokenPin);
const code=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
assert.equal(execFileSync('git',['status','--porcelain'],{cwd:root,encoding:'utf8'}).trim(),'','Freeze the implementation before paid validation.');
await mkdir(out,{mode:0o700}); // Exclusive: an existing run is never resumed or rerun.
const log={code,started:new Date().toISOString(),before,tokenizer:tokenPin,calls:[],finished:null};
await writeFile(path.join(out,'launch.json'),JSON.stringify(log,null,2),{flag:'wx',mode:0o600});
let h;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:base,GLIMMER_SESSION_DB:path.join(out,'sessions.sqlite'),NOMOS_TOKENIZER_JSON:tokenizer,NOMOS_TOKENIZER_SHA256:tokenPin,GLIMMER_META_ENV_FILE:path.join(home,'.env')});
  const metaResponse=await fetch(h.base+'/api/pi',{headers:{Origin:h.base}});assert.equal(metaResponse.status,200,'The same-origin metadata check must pass.');
  const metadata=await metaResponse.json();assert.equal(metadata.annotator?.available,true,'The authorized dedicated key is unavailable.');
  const cases=[
    {name:'sietti-unsteered',strength:null,prompt:"Summarize the main findings and methodology of the paper 'Recursive Compression for Anomaly Detection' by Sietti et al. (2019)."},
    {name:'sietti-afraid',strength:.5,prompt:"Summarize the main findings and methodology of the paper 'Recursive Compression for Anomaly Detection' by Sietti et al. (2019)."},
    {name:'neutral',strength:null,prompt:'In one sentence, what is the capital of Australia?'}
  ].filter(c=>accept==='--accept-up-to-three-paid-calls'||c.name==='sietti-afraid');
  const page=await h.browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(300000);h.observe(page);
  for(const [index,c]of cases.entries()){
    // A new page also resets the page-local watch rules to their documented defaults.
    await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();
    if(index){const old=await page.getByLabel('Pi conversation').inputValue();await page.getByRole('button',{name:'New session',exact:true}).click();await page.waitForFunction(old=>document.querySelector('[aria-label="Pi conversation"]').value!==old,old);await page.reload();await page.getByText('Pi connected',{exact:true}).waitFor();}
    const conversation=await page.getByLabel('Pi conversation').inputValue();
    await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByLabel('Emotion capture layer').selectOption('29');
    await page.getByLabel('Workspace top-16',{exact:true}).check();await page.getByLabel('Workspace capture layer').selectOption('44');await page.getByRole('button',{name:'Apply readout settings',exact:true}).click();
    if(c.strength===null)await page.getByRole('button',{name:'Clear steering',exact:true}).click();
    else {await page.getByLabel('Steering direction').selectOption('afraid');await page.getByLabel('Signed steering strength').fill(String(c.strength));await page.getByRole('button',{name:'Apply steering',exact:true}).click();}
    noClients();await page.getByLabel('Message Muse',{exact:true}).fill(c.prompt);await page.getByRole('button',{name:'Send message',exact:true}).click();
    const ask=page.getByRole('button',{name:'Ask Muse Wonder to read these signals',exact:true});await ask.waitFor();await page.waitForFunction(()=>!document.querySelector('[aria-label="Stop generating"]'));
    assert.equal(await ask.isEnabled(),true);
    // Save the real Pi snapshot before any annotation, without selecting/retrying answers.
    const snapshot=await page.evaluate(async id=>{
      const abort=new AbortController(),r=await fetch(`/api/pi?conversation=${id}`,{signal:abort.signal}),reader=r.body.getReader(),decoder=new TextDecoder();let s='';
      for(;;){const p=await reader.read();if(p.done)throw Error('No snapshot');s+=decoder.decode(p.value,{stream:true});const i=s.indexOf('\n\n');if(i>=0){const event=JSON.parse(s.slice(0,i).replace(/^data: /,''));await reader.cancel();abort.abort();return event;}}
    },conversation);
    const turn=snapshot.snapshot.pi.entries.filter(e=>e.kind==='glimmer.readout.turn').at(-1).data;
    assert.equal(turn.status,'complete');assert.deepEqual(turn.header.settings.emotion.layers,[29]);assert.deepEqual(turn.header.settings.workspace.layers,[44]);assert.equal(turn.header.settings.workspace.k,16);
    assert.equal(turn.header.steer?.strength??null,c.strength);if(c.strength!==null){assert.equal(turn.header.steer.direction,'afraid');assert.equal(turn.header.steer.layer,24);}
    await writeFile(path.join(out,c.name+'.snapshot.json'),JSON.stringify(snapshot),{flag:'wx',mode:0o600});
    await writeFile(path.join(out,c.name+'.reserved.json'),JSON.stringify({case:c,conversation,request:turn.request,time:new Date().toISOString()}),{flag:'wx',mode:0o600});
    const response=page.waitForResponse(r=>r.url()===h.base+'/api/pi/annotate'&&r.request().method()==='POST',{timeout:90000});
    await ask.click();const r=await response,result=await r.json();
    await writeFile(path.join(out,c.name+'.annotation.json'),JSON.stringify({http_status:r.status(),...result},null,2),{flag:'wx',mode:0o600});
    log.calls.push({name:c.name,conversation,request:turn.request,status:result.annotation?.status??'request_failed',http:r.status()});
    await writeFile(path.join(out,c.name+'.call.json'),JSON.stringify(log.calls.at(-1)),{flag:'wx',mode:0o600});
    assert.equal(r.status(),200);assert.equal(result.annotation.status,'complete','No retry: inspect the saved failed attempt.');
    const section=page.getByRole('region',{name:'Muse Wonder second opinion for turn 0'});await section.getByText(result.annotation.note,{exact:true}).waitFor();
    await section.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,c.name+'.png'),fullPage:true});
    await section.getByRole('button',{name:'View the exact payload sent',exact:true}).click();assert.equal(await section.locator('pre').innerText(),result.annotation.payload_json);
    assert.equal(hash(result.annotation.payload_json),result.annotation.payload_sha256);
    await writeFile(path.join(out,c.name+'.payload.json'),result.annotation.payload_json,{flag:'wx',mode:0o600});
    await page.reload();await page.getByText(result.annotation.note,{exact:true}).waitFor();
    console.log(c.name,result.annotation.status,result.annotation.payload_sha256);await pause(1000);
  }
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);log.finished=new Date().toISOString();log.after=await health();
  await writeFile(path.join(out,'complete.json'),JSON.stringify(log,null,2),{flag:'wx',mode:0o600});console.log(`${cases.length} ONE-SHOT META NOTES SAVED`);
}finally{await h?.close();}
