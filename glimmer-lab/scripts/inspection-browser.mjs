import assert from 'node:assert/strict';
import http from 'node:http';
import {writeFile,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {harness,root} from './harness.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
const f=await fakeKernel(),calls=[],key='inspection-fixture-not-a-credential';
const note='The reasoning and answer carry direction projections. These are descriptive internal signals, not a verdict. Check substantive claims against a source.';
const meta=http.createServer(async(req,res)=>{let body='';for await(const b of req)body+=b;assert.equal(req.headers.authorization,`Bearer ${key}`);calls.push(body);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({model:'muse-wonder-1.0',choices:[{finish_reason:'stop',message:{content:note}}]}));});
await new Promise(r=>meta.listen(0,'127.0.0.1',r));
await writeFile(f.root+'/fake.env',`META_API_HACKATHON=${key}\n`,{mode:0o600});
let h,page;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/pages.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin,
    NODE_OPTIONS:`--import=${root}tests/meta/mock-meta.mjs`,TEST_META_ENDPOINT:`http://127.0.0.1:${meta.address().port}`,GLIMMER_META_ENV_FILE:f.root+'/fake.env'});
  page=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});h.observe(page);page.setDefaultTimeout(15000);
  await page.goto(h.base+'/statistics');await page.getByRole('heading',{name:'No turn to measure yet'}).waitFor();
  const nav=page.locator('nav[aria-label="Lab pages"]');
  await nav.getByRole('link',{name:'Chat',exact:true}).click();await until(async()=>await page.getByLabel('Message Muse',{exact:true}).isEnabled());
  await page.getByLabel('Message Muse',{exact:true}).fill('A saved greeting for the inspection pages');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('link',{name:'Inspect this turn’s workspace →',exact:true}).waitFor();
  await nav.getByRole('link',{name:'Statistics',exact:true}).click();await page.locator('.trace-summary strong').getByText('+5.45',{exact:true}).waitFor();
  assert.equal(await page.locator('.metric').nth(0).locator('strong').innerText(),'38');assert.equal(await page.locator('.metric').nth(1).locator('strong').innerText(),'2');
  assert.equal(await page.locator('.metric').nth(2).locator('strong').innerText(),'Off');assert.equal(await page.locator('tbody').first().locator('tr').count(),7);
  await page.locator('.statistics-page .prompt').getByText('A saved greeting for the inspection pages',{exact:true}).waitFor();
  await page.screenshot({path:root+'preview/checks/statistics-fixture.png'});
  await page.getByLabel('Statistics channel').selectOption('answer');assert.equal(await page.locator('.trace-summary strong').innerText(),'+15.00');
  await page.getByLabel('Statistics direction').selectOption('calm');assert.equal(await page.locator('.trace-summary strong').innerText(),'+12.00');
  await page.reload();await page.locator('.trace-summary strong').getByText('+5.45',{exact:true}).waitFor();assert.equal(f.requests.length,1);
  await nav.getByRole('link',{name:'Interpreter',exact:true}).click();await page.getByRole('heading',{name:'What Muse Wonder receives'}).waitFor();assert.equal(calls.length,0);
  await page.screenshot({path:root+'preview/checks/interpreter-pending-fixture.png'});
  const ask=page.getByRole('region',{name:'Interpreter note'}).getByRole('button',{name:'Ask Muse Wonder to read these signals',exact:true});await ask.click();
  await page.getByRole('region',{name:'Interpreter note'}).getByText(note,{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Exact request',exact:true}).click();assert.equal(await page.getByTestId('interpreter-payload').innerText(),calls[0]);assert.equal(calls.length,1);
  assert.ok((await page.locator('.payload-hash').innerText()).includes(createHash('sha256').update(calls[0]).digest('hex')));
  await page.getByRole('tab',{name:'Signal summary',exact:true}).click();await page.screenshot({path:root+'preview/checks/interpreter-complete-fixture.png'});
  await page.reload();await page.getByRole('heading',{name:'Prepared signal payload'}).waitFor();assert.equal(calls.length,1);assert.equal(f.requests.length,1);assert.equal((await page.content()).includes(key),false);
  await nav.getByRole('link',{name:'System',exact:true}).click();await page.locator('.metric').first().getByText('Ready',{exact:true}).waitFor();
  assert.equal(f.cacheRequests(),0);await page.getByRole('button',{name:'Read cache counters',exact:true}).click();await page.getByText('75.0%',{exact:true}).waitFor();
  assert.equal(f.cacheRequests(),1);await page.getByRole('button',{name:'Read cache counters',exact:true}).click();await page.getByText(/Cache refresh is limited/).waitFor();assert.equal(f.cacheRequests(),1);
  await page.getByRole('region',{name:'UI host resources'}).getByText('Local GPUs · not backend telemetry',{exact:true}).waitFor();
  await page.screenshot({path:root+'preview/checks/system-fixture.png'});
  assert.equal((await page.request.get(h.base+'/api/system',{headers:{Origin:'http://evil.test'}})).status(),403);
  assert.equal((await page.request.get(h.base+'/api/system?endpoint=http://evil.test',{headers:{Origin:h.base}})).status(),400);
  assert.equal((await page.request.post(h.base+'/api/system',{headers:{Origin:h.base}})).status(),405);
  for(const path of ['/statistics','/interpreter','/system']){
    await nav.getByRole('link',{name:path==='/statistics'?'Statistics':path==='/interpreter'?'Interpreter':'System',exact:true}).click();
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),path+' fits mobile');
    await page.screenshot({path:root+`preview/checks${path}-mobile.png`,fullPage:true});await page.setViewportSize({width:1440,height:900});
  }
  const conv=await page.getByLabel('Inspection conversation').count()?await page.getByLabel('Inspection conversation').inputValue():new URL(page.url()).searchParams.get('conversation');
  await page.goto(`${h.base}/statistics?conversation=${conv}&turn=missing`);await page.getByRole('heading',{name:'Turn not found'}).waitFor();
  assert.equal(f.requests.length,1,'Every inspection, reload and page navigation is read-only to the model');assert.equal(calls.length,1,'Only the explicit fake-provider ask made a request');
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  // Separate observer for intentional failure states.
  const failed=await h.browser.newPage();await failed.route('**/api/system',r=>r.fulfill({status:503,contentType:'application/json',body:'{}'}));await failed.goto(h.base+'/system');await failed.getByRole('alert').getByText(/System snapshot unavailable/).waitFor();await failed.close();
  console.log('INSPECTION PAGES VERIFIED');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:root+'preview/checks/inspection-failure.png'});}throw e;}
finally{await h?.close();meta.closeAllConnections();await new Promise(r=>meta.close(r));await f.close();await rm(f.root,{recursive:true,force:true});}
