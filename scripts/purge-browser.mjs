// Production UI, fake kernel, private temporary database. No real user data.
import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {harness,root} from './harness.mjs';
import {waitForProfileSettled} from './profile-settled.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
const f=await fakeKernel();let h,page;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/purge-ui.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  const context=await h.browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  page=await context.newPage();h.observe(page);page.setDefaultTimeout(12000);
  const post=(route,body,origin=h.base)=>fetch(h.base+route,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
  const history=async()=>{const r=await fetch(h.base+'/api/pi?history=1',{headers:{Origin:h.base}});assert.equal(r.status,200);return(await r.json()).conversations;};
  const manage=()=>page.getByRole('button',{name:'Manage conversation data',exact:true});
  const dialog=()=>page.getByRole('dialog',{name:'Conversation data',exact:true});
  await page.goto(h.base+'/');await until(async()=>await page.locator('.replay-meta').innerText()==='Durable Pi session');
  await page.getByLabel('Message Muse',{exact:true}).fill('Conversation to delete');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByText('Hello from Pi.',{exact:true}).waitFor();const first=(await history())[0].id;await waitForProfileSettled(h.base,first);
  await page.getByRole('button',{name:'New conversation',exact:true}).click();await until(async()=>(await history()).length===2);
  const keep=(await history())[0].id;await waitForProfileSettled(h.base,keep);
  await until(async()=>new URL(page.url()).searchParams.get('conversation')===keep&&await page.locator('.replay-meta').innerText()==='Durable Pi session');
  await page.getByLabel('Message Muse',{exact:true}).fill('Keep this conversation');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Hello from Pi.',{exact:true}).waitFor();await waitForProfileSettled(h.base,keep);
  const peer=await context.newPage();h.observe(peer);await peer.goto(h.base+'/?conversation='+first);await peer.getByText('Conversation to delete',{exact:true}).last().waitFor();
  // A different browser context has no storage events; its old SSE bridge still clears/reconnects.
  const independent=await h.browser.newPage();h.observe(independent);await independent.goto(h.base+'/?conversation='+first);await independent.locator('.live-user').waitFor();
  await manage().click();await dialog().waitFor();assert.match(await dialog().innerText(),/Recorded examples.*model assets/s);
  await page.getByLabel('Choose a conversation').selectOption(first);await page.getByRole('button',{name:'Delete selected…',exact:true}).click();
  await page.getByRole('heading',{name:'Delete this conversation?',exact:true}).waitFor();const permanent=page.getByRole('button',{name:'Permanently delete',exact:true});
  assert.equal(await permanent.isDisabled(),true);await page.getByLabel('Type DELETE to confirm').fill('delete');assert.equal(await permanent.isDisabled(),true);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal((await history()).length,2);
  await page.getByRole('button',{name:'Delete selected…',exact:true}).click();await page.getByLabel('Type DELETE to confirm').fill('DELETE');
  await page.screenshot({path:root+'preview/checks/purge-selected-desktop.png'});
  await permanent.click();await dialog().waitFor({state:'hidden'});await until(async()=>(await history()).length===1);
  await until(async()=>(await peer.locator('.live-user').count())===0&&(await independent.locator('.live-user').count())===0);
  await until(async()=>await page.locator('.replay-meta').innerText()==='Durable Pi session');
  assert.match(await page.locator('.live-user').innerText(),/Keep this conversation/);
  assert.doesNotMatch(await peer.locator('body').innerText(),/Conversation to delete/);
  assert.doesNotMatch(await independent.locator('body').innerText(),/Conversation to delete/);
  await peer.reload();await until(async()=>await peer.locator('.replay-meta').innerText()==='Durable Pi session');
  assert.match(await peer.locator('.live-user').innerText(),/Keep this conversation/);
  // Same-origin and exact-body protection are server checks, not UI-only confirmation.
  assert.equal((await post('/api/pi/data',{action:'preview',target:{scope:'all'}},'http://evil.example')).status,403);
  assert.equal((await post('/api/pi/data',{action:'preview',target:{scope:'all',path:'/'}})).status,409);
  assert.equal((await post('/api/pi/data',{action:'purge',target:{scope:'all'},confirmation:'PURGE ALL'})).status,409);
  const preview=await(await post('/api/pi/data',{action:'preview',target:{scope:'all'}})).json();
  f.hold(true);assert.equal((await post('/api/pi',{action:'submit',conversation:keep,text:'Hold this active turn'})).status,200);await until(()=>f.requests.length===3);
  assert.equal((await post('/api/pi/data',{action:'purge',target:preview.target,revision:preview.revision,confirmation:preview.confirmation})).status,409);
  assert.equal((await post('/api/pi',{action:'abort',conversation:keep})).status,200);await waitForProfileSettled(h.base,keep);
  assert.equal((await post('/api/pi/data',{action:'purge',target:preview.target,revision:preview.revision,confirmation:preview.confirmation})).status,409,'stale confirmation');
  await until(async()=>await page.getByRole('button',{name:'Send message',exact:true}).count()===1);
  await manage().click();await page.getByRole('button',{name:'Purge all…',exact:true}).click();await page.getByLabel('Type PURGE ALL to confirm').fill('PURGE ALL');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:root+'preview/checks/purge-all-mobile.png'});
  await page.getByRole('button',{name:'Permanently purge all',exact:true}).click();await dialog().waitFor({state:'hidden'});
  await until(async()=>(await history()).length===0);
  for(const p of [page,peer,independent]){await until(async()=>await p.locator('.live-user').count()===0);await p.reload();}
  await new Promise(resolve=>setTimeout(resolve,900));assert.equal((await history()).length,0,'reload must not recreate an empty conversation');
  await page.setViewportSize({width:1440,height:900});await manage().click();await dialog().waitFor();
  assert.equal(await page.getByRole('button',{name:'Delete selected…',exact:true}).isDisabled(),true);
  await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});
  await until(async()=>await manage().evaluate(e=>e===document.activeElement));
  // Exercise the actual phone navigation route, not only resizing an open dialog.
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await manage().click();await dialog().waitFor();await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});
  await until(async()=>await page.getByRole('dialog',{name:'Lab navigation',exact:true}).getByRole('button',{name:'Manage conversation data',exact:true}).evaluate(e=>e===document.activeElement));
  await page.getByRole('button',{name:'Close navigation',exact:true}).click();
  await page.getByRole('dialog',{name:'Lab navigation',exact:true}).waitFor({state:'hidden'});
  await page.setViewportSize({width:1440,height:900});
  await page.getByRole('button',{name:'Start a conversation',exact:true}).click();await until(async()=>(await history()).length===1);
  await until(async()=>await page.locator('.replay-meta').innerText()==='Durable Pi session');assert.ok(Number((await history())[0].id)>Number(keep));
  assert.equal(f.requests.length,3,'purge and reload never submit model requests');
  assert.deepEqual(h.external,[]);
  // Expected 503s while the exclusive maintenance barrier is held are retried
  // only for read-only history. No JavaScript errors are allowed.
  const errors=h.errors.filter(e=>!(typeof e==='object'&&e.status===503)&&!(typeof e==='object'&&e.message?.includes('503')));
  assert.deepEqual(errors,[]);
  console.log('PURGE BROWSER VERIFIED');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-6500));await page.screenshot({path:root+'preview/checks/purge-failure.png',fullPage:true});}throw e;}
finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
