// CPU/browser-only recheck of a retained run. No Nomos or paid-model endpoint.
import assert from 'node:assert/strict';
import http from 'node:http';
import {constants} from 'node:fs';
import {readFile,writeFile,mkdir,copyFile,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {homedir} from 'node:os';
import {harness} from './harness.mjs';
import {outcome} from './mojo-live-result.mjs';
const [original,dest]=process.argv.slice(2);
assert.ok(original&&dest&&process.argv.length===4&&path.isAbsolute(original)&&path.isAbsolute(dest),'Use ORIGINAL_DIR NEW_OFFLINE_DIR');
const sha=b=>createHash('sha256').update(b).digest('hex');
const files=['launch.json','result.json','snapshot.json','profile.json','session.sqlite','main.mojo'];
const inputs=Object.fromEntries(await Promise.all(files.map(async name=>[name,sha(await readFile(path.join(original,name)))])));
assert.equal(await stat(path.join(original,'session.sqlite-wal')).then(()=>true,e=>{if(e.code==='ENOENT')return false;throw e;}),false,'Close/checkpoint the source before copying');
const load=async name=>JSON.parse(await readFile(path.join(original,name),'utf8'));
const launch=await load('launch.json'),record=await load('result.json'),saved=await load('profile.json'),snap=await load('snapshot.json');
assert.equal(launch.mode,'--live');assert.equal(record.submitted,true);assert.equal(record.settled,true);
assert.equal(record.after.ready,true);assert.equal(record.after.readout.supported,true);
const marker='glimmer-mojo-live-ok',source=`def main():\n    print("${marker}")\n`,bytes=await readFile(path.join(original,'main.mojo'));
const checked=outcome(snap,bytes,saved,source,marker);
await mkdir(dest,{mode:0o700});
await copyFile(path.join(original,'session.sqlite'),path.join(dest,'session.sqlite'),constants.COPYFILE_EXCL);
assert.equal(sha(await readFile(path.join(dest,'session.sqlite'))),inputs['session.sqlite']);
const unexpected=[];let healthRequests=0,h;
const fake=http.createServer((req,res)=>{
  res.setHeader('Content-Type','application/json');
  if(req.url==='/health'&&req.method==='GET'){healthRequests++;res.end(JSON.stringify(launch.before));}
  else{unexpected.push({method:req.method,url:req.url});res.statusCode=503;res.end('{"error":"Offline evidence viewer: no model route"}');}
});
await new Promise(resolve=>fake.listen(0,'127.0.0.1',resolve));
try{
  const tokenizer=path.join(homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json');
  assert.equal(sha(await readFile(tokenizer)),launch.tokenizer);
  h=await harness(false,{NOMOS_API_BASE_URL:`http://127.0.0.1:${fake.address().port}`,GLIMMER_SESSION_DB:path.join(dest,'session.sqlite'),
    GLIMMER_META_ENV_FILE:path.join(dest,'no-meta-key.env'),GLIMMER_MOJO_PREFIX:'',NOMOS_TOKENIZER_JSON:tokenizer,NOMOS_TOKENIZER_SHA256:launch.tokenizer});
  const page=await h.browser.newPage({viewport:{width:1500,height:1100}});h.observe(page);page.setDefaultTimeout(30000);
  for(const name of ['loaded','reloaded']){
    if(name==='loaded')await page.goto(h.base);else await page.reload();
    await page.getByText('Pi connected',{exact:true}).waitFor();
    assert.equal(await page.getByLabel('Pi conversation').inputValue(),String(snap.snapshot.settings.conversationId));
    const tool=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/mojo Completed/})});
    await tool.getByRole('button').click();
    assert.equal((await tool.getByLabel('Mojo run stdout',{exact:true}).innerText()).trim(),marker);
    assert.match(await tool.getByLabel('Mojo compile output',{exact:true}).innerText(),/exit 0/);
    assert.match(await tool.getByLabel('Mojo run output',{exact:true}).innerText(),/exit 0/);
    const displayed=JSON.parse(await tool.getByLabel('mojo result',{exact:true}).innerText());assert.deepEqual(displayed,checked.mojo);
    await page.screenshot({path:path.join(dest,`${name}.png`),fullPage:true});
  }
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);assert.deepEqual(unexpected,[]);assert.ok(healthRequests>0);
  for(const [name,pin]of Object.entries(inputs))assert.equal(sha(await readFile(path.join(original,name))),pin,'Source changed: '+name);
  await writeFile(path.join(dest,'result.json'),JSON.stringify({status:'pass',scope:'offline saved-record and cloned-session UI recheck; not another live run',
    originalStatus:record.status,originalFailure:record.failure,inputs,checked,healthRequests,modelRequests:unexpected.length},null,2)+'\n',{flag:'wx',mode:0o600});
  console.log('OFFLINE MOJO RECORD AND UI VERIFIED; model requests 0; originals unchanged');
}finally{await h?.close();fake.closeAllConnections();await new Promise(resolve=>fake.close(resolve));}
