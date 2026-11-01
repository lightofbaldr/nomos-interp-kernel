// Explicit GPU CLIENT gate against the existing serve. Never a launcher for the kernel.
import assert from 'node:assert/strict';
import {execFileSync,spawn} from 'node:child_process';
import {readFile,mkdtemp,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import path from 'node:path';
import {harness,pause} from './harness.mjs';
const base='http://127.0.0.1:8091',home=homedir();
const kernel=process.env.NOMOS_KERNEL_DIR??path.join(home,'Projects/nomos-interp-kernel');
const agent=process.env.GLIMMER_AGENT_DIR??path.join(home,'Projects/glimmer-agent');
const rev='5dcc2e403deca0a42273bf2cfa9bee62814b6146',so='c660097f7bec116210c06ed67e83bc07139b8eea3da850cec7d45d9a1f0628f9';
const hash=b=>createHash('sha256').update(b).digest('hex');
function noClients(){const s=execFileSync('ss',['-Htn','state','established','( sport = :8091 )'],{encoding:'utf8'});assert.equal(s.trim(),'','Another client is using the serve');}
async function identity(){assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:kernel,encoding:'utf8'}).trim(),rev);assert.equal(hash(await readFile(path.join(kernel,'libnomos_kernel-muse.so'))),so);}
await identity();noClients();
const health=await(await fetch(base+'/health',{headers:{Connection:'close'}})).json();assert.equal(health.ready,true);assert.equal(health.readout?.supported,true);
const tokenizer=path.join(home,'nomos_data/muse-glimmer-tokenizer/tokenizer.json'),tokenPin='c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d';
assert.equal(hash(await readFile(tokenizer)),tokenPin);
const evidence=await mkdtemp(path.join(home,'nomos_data/readout-spine/live-lab-'));
let h;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:base,GLIMMER_SESSION_DB:path.join(evidence,'ui.sqlite'),NOMOS_TOKENIZER_JSON:tokenizer,NOMOS_TOKENIZER_SHA256:tokenPin});
  const page=await h.browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(180000);h.observe(page);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByLabel('Workspace top-16',{exact:true}).check();
  await page.getByRole('button',{name:'Apply readout settings',exact:true}).click();
  await page.getByLabel('Signed steering strength').fill('-0.1');await page.getByRole('button',{name:'Apply steering',exact:true}).click();
  await page.getByTestId('steer-setting').filter({hasText:'afraid -0.1 at L24'}).waitFor();
  noClients();await page.getByLabel('Message Muse',{exact:true}).fill('What is 1 + 1? Answer in one short sentence.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('tab',{name:'Status',exact:true}).click();await page.locator('.turn-status summary').filter({hasText:'complete'}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('[aria-label="Stop generating"]'));
  const id=await page.getByLabel('Pi conversation').inputValue();
  // Obtain the bridge snapshot independently of rendered text/numbers.
  const snapshot=await page.evaluate(async id=>{
    const abort=new AbortController(),r=await fetch(`/api/pi?conversation=${id}`,{signal:abort.signal});
    const reader=r.body.getReader(),decoder=new TextDecoder();let s='';
    for(;;){const part=await reader.read();if(part.done)throw Error('No snapshot');s+=decoder.decode(part.value,{stream:true});const i=s.indexOf('\n\n');if(i>=0){const event=JSON.parse(s.slice(0,i).replace(/^data: /,''));await reader.cancel();abort.abort();return event;}}
  },id);
  assert.equal(snapshot.type,'bridge.snapshot');const t=snapshot.snapshot.pi.entries.find(e=>e.kind==='glimmer.readout.turn').data;
  assert.equal(t.status,'complete');assert.equal(t.records.length,t.summary.counts.records);const gen=t.records.filter(r=>r.source==='generated');
  assert.ok(gen.length>0&&gen.every(r=>r.emotion&&r.workspace));assert.equal(t.header.steer.strength,-.1);
  const last=gen.at(-1),li=t.header.settings.emotion.layers.indexOf(29),ei=t.header.settings.emotion.names.indexOf('afraid');
  await page.getByRole('tab',{name:'Signals',exact:true}).click();assert.match(await page.locator('.live-dock').innerText(),new RegExp(last.emotion.proj[li][ei].toFixed(2).replace('.','\\.')));
  assert.equal(await page.locator('.workspace-tokens li').count(),16);
  await page.screenshot({path:path.join(evidence,'ui.png'),fullPage:true});
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  await writeFile(path.join(evidence,'ui-result.json'),JSON.stringify({kernel:rev,library:so,health,header:t.header,summary:t.summary,records:t.records.length,generated:gen.length,tokenizer:tokenPin},null,2),{flag:'wx'});
}finally{await h?.close();}
await pause(1500);noClients();
// Re-run Plan 3's original two-turn PI E2E client, not its serve-down launcher.
const source=path.join(agent,'scripts/e2e-gpu.ts'),sourceSha=hash(await readFile(source));
const expected=execFileSync('git',['show','6ea3078:scripts/e2e-gpu.ts'],{cwd:agent});assert.equal(sourceSha,hash(expected),'The reviewed PI E2E client changed');
const result=await new Promise(resolve=>{
  const child=spawn(process.execPath,[source,base+'/v1',evidence],{cwd:agent,stdio:['ignore','pipe','pipe']});let output='';
  child.stdout.on('data',b=>{output+=b;process.stdout.write(b);});child.stderr.on('data',b=>{output+=b;process.stderr.write(b);});
  child.on('error',e=>resolve({code:1,output:String(e)}));child.on('exit',code=>resolve({code,output}));
});
await writeFile(path.join(evidence,'pi-e2e.log'),result.output,{flag:'wx'});assert.equal(result.code,0);assert.match(result.output,/PI E2E OK/);
await identity();const after=await(await fetch(base+'/health',{headers:{Connection:'close'}})).json();assert.equal(after.ready,true);
await writeFile(path.join(evidence,'complete.json'),JSON.stringify({status:'pass',piClientSha256:sourceSha,ui:hash(await readFile(path.join(evidence,'ui-result.json'))),pi:hash(await readFile(path.join(evidence,'result.json'))),serveReady:after.ready},null,2),{flag:'wx'});
console.log('Evidence:',evidence);console.log('LIVE PI E2E VERIFIED');
