import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {harness,root,pause} from './harness.mjs';
import {fakeKernel,thought,answer} from './fake-kernel.mjs';
import {SYSTEM_MESSAGE} from '../src/lib/live.ts';
const fixture=await fakeKernel(),h=await harness(false,{NOMOS_API_BASE_URL:fixture.url}),checks=[];
const check=(value,label)=>{assert.ok(value,label);checks.push(label);};
async function settled(page){await page.getByRole('button',{name:'Send message',exact:true}).waitFor();await page.waitForFunction(()=>!document.querySelector('.live-assistant[data-status=streaming]'));}
async function send(page,text){await page.getByLabel('Message Muse',{exact:true}).fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();}
try{
  const page=await h.browser.newPage({viewport:{width:1440,height:980},reducedMotion:'reduce'});h.observe(page);await page.goto(h.base,{waitUntil:'networkidle'});await page.getByRole('button',{name:'Live kernel',exact:true}).waitFor();
  check(await page.locator('.live-assistant').count()===0,'Live starts with a blank conversation, never fixtures');
  check(await page.getByText('Not measured',{exact:true}).isVisible()&&await page.locator('.map-concept').count()===0,'Connected chat is not measured workspace/emotion data');
  await page.waitForFunction(()=>document.querySelector('.kv-counters')?.textContent.includes('1.00 MiB'));
  check((await page.locator('.kv-counters').innerText()).includes('2.00 MiB'),'Measured RAM/NVMe bytes appear with real units');
  await send(page,'What is the capital of France?');await settled(page);
  check(await page.locator('.live-assistant .response-text').textContent()===answer,'Answer deltas are joined exactly');
  await page.getByRole('button',{name:'Show reasoning',exact:true}).click();check(await page.locator('.reasoning-text').textContent()===thought,'Provider reasoning appears separately and keeps whitespace/emoji');
  await send(page,'Follow up.');await settled(page);
  const second=fixture.state.requests[1];check(second.messages[0].content===SYSTEM_MESSAGE&&fixture.state.requests[0].messages[0].content===SYSTEM_MESSAGE,'System prompt is fixed, with reasoning high and no changing date');
  check(second.messages[2].content===answer&&second.messages[2].reasoning_content===thought,'Follow-up carries exact answer and reasoning, not rendered or trimmed text');
  check(await page.locator('.emotion-steering').getAttribute('data-applied')==='false','Real chat does not activate preview steering');
  await send(page,'[slow]');await page.waitForFunction(()=>document.querySelectorAll('.live-reasoning-trigger').length===3);const reads=fixture.state.statsRequests;await pause(450);check(fixture.state.statsRequests===reads,'No KV-stat polling while generation holds the engine');
  await page.getByRole('button',{name:'Stop generating',exact:true}).click();await settled(page);await pause(300);check(fixture.state.aborted>=1&&fixture.state.active===0,'Stop propagates through the proxy and closes the upstream stream');
  await send(page,'After the stop.');await settled(page);const afterStop=fixture.state.requests.at(-1);check(afterStop.messages.at(-2).reasoning_content==='  partial reasoning 🙂\n'&&afterStop.messages.at(-2).content==='','Stopped partial reasoning is kept verbatim in the next context');
  await send(page,'[interrupt]');await settled(page);check((await page.getByRole('alert').innerText()).includes('before completion'),'Premature EOF is reported, not silently marked complete');
  check(await page.locator('.live-assistant').last().getAttribute('data-status')==='error','Interrupted reply carries an explicit error status');
  await send(page,'[noanswer]');await settled(page);check((await page.locator('.live-assistant').last().innerText()).includes('No answer text was received'),'Reasoning-only budget exhaustion remains visible, not an empty body');
  await page.getByRole('button',{name:'Show reasoning',exact:true}).last().click();check(await page.locator('.live-assistant').last().locator('.reasoning-text').textContent()===thought,'Reasoning is not thrown away at the output limit');
  await send(page,'[markup]');await settled(page);check((await page.locator('.live-assistant .response-text').last().textContent()).includes('<img'),'Model markup is visible as literal text');check(await page.locator('.live-assistant .response-text img').count()===0&&await page.evaluate(()=>window.__modelInjected)===undefined,'Model-supplied markup never executes');
  await page.locator('.history-sidebar').getByRole('button',{name:'New conversation',exact:true}).click();check(await page.locator('.live-assistant').count()===0&&await page.getByRole('button',{name:'Live kernel',exact:true}).isVisible(),'New conversation clears history without falling back to a scripted demo');
  for(const width of [320,375,768,1280,1440]){
    await page.setViewportSize({width,height:width>=960?800:844});await pause(100);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Live layout has no root overflow at ${width}`);
    for(const selector of ['.muse-composer','.emotion-steering']){const b=await page.locator(selector).boundingBox();check(b&&b.x>=-1&&b.y>=-1&&b.x+b.width<=width+1&&b.y+b.height<=page.viewportSize().height+1,`Live ${selector} stays in view at ${width}`);}
    await page.screenshot({path:root+`preview/live-${width}.png`});
  }
  fixture.state.ready=false;await page.getByRole('button',{name:'Refresh kernel connection',exact:true}).click();await page.getByRole('button',{name:'Live / disconnected',exact:true}).waitFor();check(await page.getByLabel('Message Muse',{exact:true}).isDisabled(),'Offline/not-ready disables sends without inventing a response');
  check(h.errors.length===0,JSON.stringify(h.errors));check(h.external.length===0,'Browser talks only to the same-origin proxy');
  await writeFile(root+'preview/checks/chat.json',JSON.stringify({checkedAt:new Date().toISOString(),checks,fixture:'CPU-only fake kernel; not model evidence',entry:h.entry},null,2));console.log(`MUSE CHAT PASSED: ${checks.length} browser controls.`);
}finally{await h.close();await fixture.close();}
