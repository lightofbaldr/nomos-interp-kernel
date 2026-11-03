import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {harness,root} from './harness.mjs';
const h=await harness(true);
try{
  const page=await h.browser.newPage({viewport:{width:1440,height:980},reducedMotion:'reduce'});h.observe(page);await page.goto(h.base+'/preview',{waitUntil:'networkidle'});await page.getByRole('button',{name:'Live kernel',exact:true}).waitFor();
  await page.getByLabel('Message Muse',{exact:true}).fill('What is the capital of France? Answer briefly.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.locator('.live-assistant[data-status=stop]').waitFor({timeout:120000});
  const content=await page.locator('.live-assistant .response-text').textContent();assert.match(content,/Paris/i);
  await page.getByRole('button',{name:'Show reasoning',exact:true}).click();const reasoning=await page.locator('.reasoning-text').textContent();assert.ok(reasoning.trim().length>0,'Actual provider reasoning must be present');
  await page.locator('.kv-counters').waitFor();assert.equal(await page.locator('.emotion-steering').getAttribute('data-applied'),'false');assert.equal(await page.locator('.map-concept').count(),0);assert.equal(h.errors.length,0,JSON.stringify(h.errors));
  const health=await (await page.request.get(h.base+'/api/nomos/health')).json();assert.equal(health.health.ready,true);assert.match(health.health.model,/muse/i);
  await page.screenshot({path:root+'preview/kernel-reasoning-desktop.png'});
  await page.getByRole('button',{name:'Show reasoning',exact:true}).click();await page.locator('.reasoning-text').waitFor({state:'hidden'});await page.locator('.live-assistant .response-text').scrollIntoViewIfNeeded();
  await page.screenshot({path:root+'preview/kernel-chat-desktop.png'});
  for(const width of [375,768]){await page.setViewportSize({width,height:844});await page.screenshot({path:root+`preview/kernel-chat-${width}.png`});}
  await writeFile(root+'preview/checks/kernel-chat.json',JSON.stringify({checkedAt:new Date().toISOString(),model:health.health.model,content,reasoning,finish:'stop',entry:h.entry,scope:'One real UI request through the configured server; health/model are server-reported, not loaded-binary attestation.'},null,2));
  console.log('MUSE KERNEL CHAT PASSED: real streamed reasoning + Paris, natural stop, measured KV counters.');
}finally{await h.close();}
