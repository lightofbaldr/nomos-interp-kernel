// Synthetic providers, isolated database, no real kernel or paid calls.
import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {harness,root} from './harness.mjs';
import {waitForProfileSettled} from './profile-settled.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
import {consumeChat} from '../src/lib/live.ts';
const f=await fakeKernel();let h,page;
const greedy={temperature:0,top_p:1,top_k:0,max_tokens:6144},sampled={temperature:1,top_p:0.95,top_k:64,max_tokens:6144};
const params=r=>Object.fromEntries(Object.keys(greedy).map(k=>[k,r[k]]));
async function openSettings(page){
  const navigation=page.getByRole('button',{name:'Open navigation',exact:true});
  if(await navigation.isVisible()){
    await navigation.click();
    await page.getByRole('dialog',{name:'Lab navigation',exact:true}).getByRole('button',{name:'Steering & readouts',exact:true}).click();
  }else await page.locator('.lab-sidebar-bottom').getByRole('button',{name:'Steering & readouts',exact:true}).click();
}
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/generation-ui.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  page=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});h.observe(page);page.setDefaultTimeout(15000);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();
  assert.equal(await page.locator('.composer-section .decoding-control').count(),0,'Sampling belongs in Controls, not above the message input');
  assert.equal(await page.locator('.composer-section .replay-toolbar').count(),0,'Session status and reconnect belong with the settings');
  await page.getByRole('tab',{name:'Controls',exact:true}).click();
  const selector=page.getByLabel('Pi conversation'),a=await selector.inputValue(),toggle=page.getByRole('switch',{name:"Glimmer's sampling",exact:true});
  await until(async()=>await toggle.isEnabled());assert.equal(await toggle.getAttribute('aria-checked'),'false');
  assert.match(await page.locator('#sampling-cost').innerText(),/Slower.*disables speculative/);
  f.reply({thinking:'Capped reasoning is kept.',answer:'',finish:'length'});
  await page.getByLabel('Message Muse',{exact:true}).fill('The same question must occur once.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('button',{name:'Retry',exact:true}).waitFor();await waitForProfileSettled(h.base,a);
  assert.equal(f.requests.length,1);assert.deepEqual(params(f.requests[0]),greedy);
  assert.match(await page.getByTestId('turn-decoding').first().innerText(),/^Greedy.*6,144/);
  await toggle.click();await until(async()=>await toggle.getAttribute('aria-checked')==='true'&&await toggle.isEnabled());
  f.reply({thinking:'Replacement reasoning.',answer:'The replacement answer.',finish:'stop'});
  await page.getByRole('button',{name:'Retry',exact:true}).click();await page.getByText('The replacement answer.',{exact:true}).waitFor();await waitForProfileSettled(h.base,a);
  assert.deepEqual(f.requests[1].messages,f.requests[0].messages);assert.deepEqual(params(f.requests[1]),sampled);
  assert.equal(await page.locator('.live-user').count(),1);await page.getByText('Stopped at the reply limit, retried.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Retry',exact:true}).count(),0);
  assert.match(await page.getByTestId('turn-decoding').nth(0).innerText(),/^Greedy/);assert.match(await page.getByTestId('turn-decoding').nth(1).innerText(),/^Glimmer's sampling/);
  await page.reload();await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Controls',exact:true}).click();await until(async()=>await toggle.isEnabled());
  assert.equal(await toggle.getAttribute('aria-checked'),'true');assert.equal(await page.locator('.live-user').count(),1);
  await page.getByText('Stopped at the reply limit, retried.',{exact:true}).waitFor();assert.equal(f.requests.length,2);
  await page.getByRole('button',{name:'New session',exact:true}).click();await until(async()=>await selector.inputValue()!==a&&await toggle.isEnabled());
  assert.equal(await toggle.getAttribute('aria-checked'),'false');const b=await selector.inputValue();
  await selector.selectOption(a);await until(async()=>await toggle.getAttribute('aria-checked')==='true'&&await toggle.isEnabled());
  await selector.selectOption(b);await until(async()=>await toggle.getAttribute('aria-checked')==='false'&&await toggle.isEnabled());
  await selector.selectOption(a);await until(async()=>await toggle.getAttribute('aria-checked')==='true'&&await toggle.isEnabled());
  await page.goto(`${h.base}/workspace?conversation=${a}`);await page.getByTestId('workspace-decoding').filter({hasText:"Glimmer's sampling"}).waitFor();
  await page.goto(`${h.base}/statistics?conversation=${a}`);await page.getByTestId('inspection-decoding').filter({hasText:"Glimmer's sampling"}).waitFor();
  assert.match(await page.getByRole('table').last().innerText(),/Greedy/);assert.match(await page.getByRole('table').last().innerText(),/Glimmer's sampling/);
  await page.goto(`${h.base}/?conversation=${a}`);await page.getByText('Stopped at the reply limit, retried.',{exact:true}).waitFor();
  await page.screenshot({path:root+'preview/checks/generation-retry-desktop.png'});
  assert.equal(await toggle.count(),0);
  await openSettings(page);await until(async()=>await toggle.isEnabled());
  await page.screenshot({path:root+'preview/checks/generation-controls-desktop.png'});
  await page.getByRole('button',{name:'Close controls',exact:true}).click();
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.ok((await page.locator('.lab-chat .conversation-scroll').boundingBox()).height>=300,'Mobile keeps a readable reply viewport');
  await page.getByText('The replacement answer.',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:root+'preview/checks/generation-retry-mobile.png',fullPage:true});
  assert.equal(await toggle.count(),0,'The compact chat does not expose settings outside its dialog');
  await openSettings(page);
  const settings=page.getByRole('dialog',{name:'Steering & readout capture',exact:true});await settings.waitFor();
  assert.equal(await settings.getByRole('switch',{name:"Glimmer's sampling",exact:true}).getAttribute('aria-checked'),'true');
  assert.match(await settings.locator('#sampling-cost').innerText(),/Slower.*disables speculative/);
  await toggle.scrollIntoViewIfNeeded();
  await page.screenshot({path:root+'preview/checks/generation-controls-mobile.png',fullPage:true});
  assert.ok(await settings.evaluate(el=>el.scrollWidth<=el.clientWidth),'Sampling settings fit the mobile dialog');
  // Closing the dialog during a save must not bypass the existing submit guard.
  let releaseSave;const saveBarrier=new Promise(resolve=>releaseSave=resolve);
  await page.route('**/api/pi',async route=>{
    if(route.request().method()==='POST'&&route.request().postDataJSON()?.action==='generation')await saveBarrier;
    await route.continue();
  });
  try{
    await toggle.click();await until(async()=>await toggle.isDisabled());
    await page.getByRole('button',{name:'Close controls',exact:true}).click();await toggle.waitFor({state:'detached'});
    await page.getByLabel('Message Muse',{exact:true}).fill('Unsent pending-save guard fixture');
    assert.equal(await page.getByRole('button',{name:'Send message',exact:true}).isDisabled(),true);
  }finally{releaseSave();}
  await until(async()=>await page.getByRole('button',{name:'Send message',exact:true}).isEnabled());
  await page.unroute('**/api/pi');await page.getByLabel('Message Muse',{exact:true}).fill('');
  await openSettings(page);await until(async()=>await toggle.isEnabled());assert.equal(await toggle.getAttribute('aria-checked'),'false');
  await page.getByRole('button',{name:'Close controls',exact:true}).click();
  assert.equal(f.requests.length,2,'Opening settings never generates a reply');
  // No selected conversation: the moved status is inside settings and the
  // greedy default is visible but cannot be changed or create a session.
  const empty=await h.browser.newPage();h.observe(empty);const emptyWrites=[];
  try{
    empty.on('request',r=>{if(r.method()!=='GET')emptyWrites.push(r.url());});
    const status=await fetch(h.base+'/api/pi',{headers:{Origin:h.base}});assert.equal(status.status,200);const data=await status.json();assert.equal(data.health.ready,true);
    await empty.route('**/api/pi*',route=>route.fulfill({json:{...data,conversations:[],autoCreate:false}}));
    await empty.goto(h.base+'/');await empty.getByRole('button',{name:'Start a conversation',exact:true}).waitFor();
    assert.equal(await empty.getByText('No conversation selected',{exact:true}).count(),0);
    await openSettings(empty);
    await empty.getByRole('dialog').getByText('No conversation selected',{exact:true}).waitFor();
    const disabled=empty.getByRole('switch',{name:"Glimmer's sampling",exact:true});assert.equal(await disabled.isDisabled(),true);assert.equal(await disabled.getAttribute('aria-checked'),'false');
    assert.deepEqual(emptyWrites,[]);
  }finally{await empty.close();}
  // The stateless direct route must enforce the same fixed choices, not forward arbitrary sampler fields.
  const messages=[{role:'system',content:'Fixed system'},{role:'user',content:'Direct route'}];
  const post=(route,body,origin=h.base)=>fetch(h.base+route,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
  for(const [extra,want] of [[{},greedy],[{sampling:true},sampled]]){
    const res=await post('/api/nomos/chat',{messages,...extra});assert.equal(res.status,200);
    assert.deepEqual(JSON.parse(res.headers.get('X-Glimmer-Generation')),want);assert.equal(res.headers.get('X-Glimmer-Reply-Cap'),'6144');
    await consumeChat(res,()=>{});assert.deepEqual(params(f.requests.at(-1)),want);
  }
  const before=f.requests.length;
  for(const extra of [{sampling:'true'},{sampling:null},{sampling:1},{temperature:1},{max_tokens:999999}])assert.equal((await post('/api/nomos/chat',{messages,...extra})).status,400);
  assert.equal((await post('/api/pi',{action:'generation',conversation:a,version:2,sampling:true},'http://evil.test')).status,403);
  assert.equal((await post('/api/pi',{action:'submit',conversation:a,text:'x',sampling:true})).status,400);
  assert.equal((await post('/api/pi',{action:'retry',conversation:a,target:0})).status,400);
  assert.equal((await post('/api/pi',{action:'generation',conversation:a,version:2,sampling:1})).status,400);
  assert.equal(f.requests.length,before);assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  console.log('GENERATION BROWSER PASS');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-5500));await page.screenshot({path:root+'preview/checks/generation-failure.png',fullPage:true});}throw e;}
finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
