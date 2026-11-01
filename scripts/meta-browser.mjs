import assert from 'node:assert/strict';
import http from 'node:http';
import {writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {harness,pause,root} from './harness.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
const f=await fakeKernel(),key='browser-fixture-not-a-credential',calls=[];
const note="With afraid steering at −0.5 on L24, the ' cannot' token entered the reasoning workspace twice. The answer is a greeting. Check any substantive claims against a source.";
const meta=http.createServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;
  assert.equal(req.url,'/v1/chat/completions');assert.equal(req.headers.authorization,`Bearer ${key}`);calls.push(body);
  await pause(200);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({model:'muse-wonder-1.0',choices:[{finish_reason:'stop',message:{content:calls.length===1?note:'word '.repeat(131)}}]}));
});
await new Promise(r=>meta.listen(0,'127.0.0.1',r));
const fakeEnv=f.root+'/fake.env';await writeFile(fakeEnv,`META_API_HACKATHON=${key}\n`,{mode:0o600});
const env={NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/meta-browser.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin,
  NODE_OPTIONS:`--import=${root}tests/meta/mock-meta.mjs`,TEST_META_ENDPOINT:`http://127.0.0.1:${meta.address().port}`,GLIMMER_META_ENV_FILE:fakeEnv};
let h,page;
try{
  h=await harness(false,env);page=await h.browser.newPage({viewport:{width:1440,height:1080}});page.setDefaultTimeout(12000);h.observe(page);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByLabel('Workspace top-16',{exact:true}).check();await page.getByRole('button',{name:'Apply readout settings',exact:true}).click();
  await page.getByLabel('Signed steering strength').fill('-0.5');await page.getByRole('button',{name:'Apply steering',exact:true}).click();
  await page.getByTestId('steer-setting').filter({hasText:'afraid -0.5 at L24'}).waitFor();
  await page.getByLabel('Message Muse',{exact:true}).fill('Hello');await page.getByRole('button',{name:'Send message',exact:true}).click();
  const ask=page.getByRole('button',{name:'Ask Muse Wonder to read these signals',exact:true});await ask.waitFor();
  assert.equal(calls.length,0);await page.reload();await ask.waitFor();assert.equal(calls.length,0,'reload cannot spend');
  const section=page.getByRole('region',{name:'Muse Wonder second opinion for turn 0'});
  await section.getByText("Sends this turn's text and a summary of its readouts to Meta's API.",{exact:true}).waitFor();
  // Changing the controls after the turn must not change its steering context.
  await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByLabel('Signed steering strength').fill('0.5');await page.getByRole('button',{name:'Apply steering',exact:true}).click();
  await ask.click();await section.getByText(note,{exact:true}).waitFor();assert.equal(calls.length,1);
  const payload=JSON.parse(calls[0]),summary=JSON.parse(payload.messages[1].content);
  assert.equal(summary.steering.strength,-.5);assert.equal(summary.steering.layer,24);assert.equal(summary.text.answer.text,'Hello from Pi.');
  assert.equal(payload.model,'muse-wonder-1.0');assert.equal(summary.workspace.watch.groups[0].word,' cannot');
  assert.equal(summary.workspace.watch.groups[0].reentries,1);assert.equal(summary.emotion.warmup_required,32);
  assert.ok(Number.isFinite(summary.emotion.layers[0].steered_direction.reasoning.raw_mean_projection));assert.equal(summary.emotion.layers[0].steered_direction.name,'afraid');
  assert.equal(summary.emotion.layers[0].steered_direction.reasoning.defined,38);assert.ok(summary.emotion.layers[0].reasoning.moves.every(m=>m.raw_defined===38));
  await section.getByText("Muse Wonder's reading of these signals. A second model's interpretation, not a verdict or a lie detector.",{exact:true}).waitFor();
  await section.getByRole('button',{name:'View the exact payload sent',exact:true}).click();assert.equal(await section.locator('pre').innerText(),calls[0]);
  assert.ok((await section.innerText()).includes(createHash('sha256').update(calls[0]).digest('hex')));
  assert.equal((await page.content()).includes(key),false);assert.equal(h.external.length,0);
  await page.screenshot({path:'preview/checks/meta-fake-desktop.png',fullPage:true});
  await page.reload();await section.getByText(note,{exact:true}).waitFor();assert.equal(calls.length,1);
  const conversation=await page.getByLabel('Pi conversation').inputValue();
  // Deliberately invalid requests use a separate client, not the observed browser.
  const post=(body,headers={})=>fetch(h.base+'/api/pi/annotate',{method:'POST',headers:{Origin:h.base,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});
  assert.equal((await post({},{Origin:'http://evil.test'})).status,403);
  assert.equal((await post({}, {'Content-Type':'text/plain'})).status,400);
  assert.equal((await post({conversation,request:'missing',consent:false,rules:{}})).status,400);
  assert.equal((await post({conversation,request:'missing',consent:true,rules:{},endpoint:'http://evil.test'})).status,400);
  assert.equal((await post({text:'x'.repeat(33000)})).status,400);assert.equal(calls.length,1);
  await page.setViewportSize({width:390,height:844});await section.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'preview/checks/meta-fake-mobile.png',fullPage:true});assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  await page.setViewportSize({width:1440,height:1080});await page.getByRole('button',{name:'New session',exact:true}).click();await until(async()=>await page.getByLabel('Pi conversation').inputValue()!==conversation);
  await page.getByLabel('Message Muse',{exact:true}).fill('Rejection fixture');await page.getByRole('button',{name:'Send message',exact:true}).click();await ask.waitFor();await ask.click();
  await page.getByText(/Rejected by: word_limit · 131 words/).waitFor();assert.equal(calls.length,2);await page.reload();await page.getByText(/Rejected by: word_limit · 131 words/).waitFor();assert.equal(calls.length,2);assert.equal(await ask.count(),0);
  await h.close();h=await harness(false,{...env,GLIMMER_META_ENV_FILE:f.root+'/does-not-exist.env'});
  page=await h.browser.newPage();h.observe(page);await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByLabel('Pi conversation').selectOption(conversation);await sectionFor(page).getByText(note,{exact:true}).waitFor();assert.equal(calls.length,2,'saved note survives missing key');
  await page.getByLabel('Message Muse',{exact:true}).fill('Next');await page.getByRole('button',{name:'Send message',exact:true}).click();await askFor(page).waitFor();
  assert.equal(await askFor(page).isDisabled(),true);await page.getByText('Muse Wonder unavailable: the dedicated server key is missing or unreadable.',{exact:true}).waitFor();assert.equal(calls.length,2);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);console.log('META ANNOTATOR BROWSER VERIFIED');
}finally{await h?.close();meta.closeAllConnections();await new Promise(r=>meta.close(r));await f.close();}
function sectionFor(page){return page.getByRole('region',{name:'Muse Wonder second opinion for turn 0'});}
function askFor(page){return page.getByRole('button',{name:'Ask Muse Wonder to read these signals',exact:true});}
