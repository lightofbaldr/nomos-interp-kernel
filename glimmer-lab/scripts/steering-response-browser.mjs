// Read-only real-bundle checks plus explicit corrupted/delayed-response controls.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {harness,root} from './harness.mjs';
import {DEFAULT_STEERING_DATA} from '../src/lib/server/steering-data.ts';
import {scenarioFixture} from '../tests/steering/scenario-fixture.ts';
const data=process.env.GLIMMER_STEERDIFF_DATA_DIR??DEFAULT_STEERING_DATA;
const catalog=JSON.parse(await readFile(data+'/catalog.json','utf8'));
const h=await harness(false,{GLIMMER_STEERDIFF_DATA_DIR:data}),checks=[],requests=[],errors=[];
const check=(value,label)=>{assert.ok(value,label);checks.push(label);};
const read=async(id,path)=>JSON.parse(await readFile(`${data}/${id}/${path}`,'utf8'));
try{
  const page=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  const ready=path=>page.waitForFunction(p=>document.querySelector('.page-footer')?.textContent.includes(p),path);
  const trigger=page.getByRole('button',{name:'Full steered response',exact:true});
  const dialog=page.getByRole('dialog',{name:'Full steered response',exact:true});
  const close=async()=>{await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});};
  const verify=async b=>{
    for(const side of ['steered','baseline'])assert.equal(await dialog.locator(`.response-card.${side} .response-text`).textContent(),b.text[side].answer||'No answer text in this export.');
    check(await dialog.locator('.response-card').first().getAttribute('class')==='response-card steered','Steered answer is first, including on mobile');
    check(await page.locator('#responses').count()===1&&await page.locator('#full-responses').count()===1,'Reference and response have distinct anchors');
  };
  await page.goto(h.base+'/steering-diff');await ready('S1/joy_0.7.matched.json');
  const initial=await read('dataset-1','S1/joy_0.7.free.json');
  check(!requests.some(r=>r.url.endsWith('S1/joy_0.7.free.json')),'No free-response fetch before explicit opening');
  const position=await page.getByRole('spinbutton',{name:'Position',exact:true}).inputValue();
  await trigger.focus();await trigger.press('Enter');await dialog.locator('.response-card.steered').waitFor();
  await verify(initial);
  check((await dialog.textContent()).includes('separate free-generation capture'),'Matched-view text is explicitly from a separate capture');
  check(await page.getByRole('tab',{name:'Matched input replay',exact:true,includeHidden:true}).getAttribute('aria-selected')==='true','Opening text does not switch the workspace mode');
  const steered=dialog.locator('.response-card.steered');
  await steered.getByRole('button',{name:'Recorded reasoning / envelope text',exact:true}).click();
  assert.equal(await steered.locator('.reasoning-text').textContent(),initial.text.steered.reasoning||'No reasoning text in this export.');
  check(true,'Exported reasoning remains available separately and exactly');
  await dialog.screenshot({path:root+'preview/checks/full-steered-response-desktop.png'});
  await close();await page.waitForFunction(()=>document.activeElement?.classList.contains('full-response-trigger'));check(true,'Escape returns keyboard focus to the response control');
  check(await page.getByRole('spinbutton',{name:'Position',exact:true}).inputValue()===position,'Readout position is unchanged after reading the response');
  // Free generation opens only the same selected file, verified again on demand.
  await page.getByRole('tab',{name:'Free generation',exact:true}).click();await ready('S1/joy_0.7.free.json');
  const before=requests.filter(r=>r.url.endsWith('S1/joy_0.7.free.json')).length;
  await trigger.click();await dialog.locator('.response-card.steered').waitFor();await verify(initial);
  check(requests.filter(r=>r.url.endsWith('S1/joy_0.7.free.json')).length===before+1,'Only the selected free response is fetched and rechecked on demand');await close();
  // Exact source-linked replay -> answer scenario, without changing the replay.
  const lies=catalog.datasets.find(d=>d.run==='run-20261026T173011-lies');assert.ok(lies);
  const scenario=page.locator('select[aria-label="Scenario"]'),arm=page.locator('select[aria-label="Intervention"]');
  await scenario.selectOption(`${lies.id}:TATHUGA-replay`);await arm.selectOption('anger_0.5');
  await page.getByRole('tab',{name:'Matched input replay',exact:true}).click();await ready('TATHUGA-replay/anger_0.5.matched.json');
  const own=await read(lies.id,'TATHUGA-answer/anger_0.5.free.json');
  await trigger.click();await dialog.locator('.response-card.steered').waitFor();await verify(own);
  check((await dialog.locator('.response-origin-note').textContent()).includes('different prefixes'),'Linked answer discloses different scenario prefixes');
  check(await scenario.inputValue()===`${lies.id}:TATHUGA-replay`,'Related answer leaves the replay scenario selected');
  await page.setViewportSize({width:390,height:844});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Full response dialog has no mobile page overflow');
  check(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1),'Full response content wraps inside the dialog');
  await dialog.evaluate(e=>e.scrollTop=e.scrollHeight);await dialog.locator('.footnote').scrollIntoViewIfNeeded();
  check(await dialog.locator('.footnote').isVisible(),'The response end and truncation disclosure remain reachable');
  await dialog.evaluate(e=>e.scrollTop=0);await page.screenshot({path:root+'preview/checks/full-steered-response-mobile.png'});
  await close();await page.setViewportSize({width:1440,height:900});
  // A corrupted own-generation file cannot be replaced with the fixed reference.
  await page.route('**/TATHUGA-answer/fear_0.5.free.json',async route=>{const r=await route.fetch(),b=await r.json();b.text.steered.answer='FORGED RESPONSE';await route.fulfill({json:b});});
  await arm.selectOption('fear_0.5');await ready('TATHUGA-replay/fear_0.5.matched.json');
  await trigger.click();await dialog.getByRole('alert').waitFor();
  check(await dialog.locator('.response-text').count()===0,'Integrity failure shows no substituted reference or prior response');
  check(!(await dialog.textContent()).includes('FORGED RESPONSE'),'Tampered response is never displayed');await close();
  // Changing selection while a response fetch is pending unmounts/cancels it.
  let release,started;const gate=new Promise(r=>release=r),seen=new Promise(r=>started=r);
  await page.route('**/TATHUGA-answer/sadness_0.5.free.json',async route=>{started();await gate;try{await route.continue();}catch{/* The canceled request may already have closed. */}});
  await arm.selectOption('sadness_0.5');await ready('TATHUGA-replay/sadness_0.5.matched.json');
  await trigger.click();await seen;
  await arm.evaluate(e=>{e.value='joy_0.5';e.dispatchEvent(new Event('change',{bubbles:true}));});release();
  await ready('TATHUGA-replay/joy_0.5.matched.json');await dialog.waitFor({state:'detached'});
  await trigger.click();await dialog.locator('.response-card.steered').waitFor();
  await verify(await read(lies.id,'TATHUGA-answer/joy_0.5.free.json'));
  check(true,'A delayed previous arm cannot populate the next arm’s response');await close();
  // Same-sample response stays paired; no s0 fallback when selecting s1.
  const sampled=catalog.datasets.find(d=>d.run==='run-20261027T090425-ws-SIETTI');assert.ok(sampled);
  await scenario.selectOption(`${sampled.id}:SIETTI-reason`);await arm.selectOption('fear_0.5');
  await page.getByRole('tab',{name:'Free generation',exact:true}).click();
  await page.getByRole('heading',{name:'Choose a paired sample',exact:true}).waitFor();
  check(await trigger.isDisabled(),'No response is chosen before an explicit sample');
  await page.getByRole('combobox',{name:'Paired sample',exact:true}).selectOption('1');await ready('SIETTI-reason/fear_0.5.s1.free.json');
  await trigger.click();await dialog.locator('.response-card.steered').waitFor();await verify(await read(sampled.id,'SIETTI-reason/fear_0.5.s1.free.json'));
  check((await dialog.textContent()).includes('sample s1'),'Exact selected sample remains visible in response provenance');await close();
  // A replay-only export must say unavailable, even though reference text exists.
  const fixture=scenarioFixture();fixture.index.comparisons=fixture.index.comparisons.filter(r=>r.mode==='matched'&&r.arm==='joy_plus');
  const only=fixture.index.comparisons[0],fixed=fixture.makeBundle(only);fixed.comparison.positions=fixed.comparison.positions.slice(0,1);
  const files={'index.json':JSON.stringify(fixture.index),[only.path]:JSON.stringify(fixed)};
  const hashes=Object.fromEntries(Object.entries(files).map(([name,body])=>[name,createHash('sha256').update(body).digest('hex')]));
  const fixtureCatalog={schema:'glimmer.local-catalog/1',datasets:[{id:'fixture',run:fixture.index.run,index:'fixture/index.json',hashes}]};
  const missing=await h.browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce'});
  missing.on('pageerror',e=>errors.push(e.message));
  missing.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  await missing.route('**/data/**',route=>{const path=new URL(route.request().url()).pathname,body=path==='/data/catalog.json'?JSON.stringify(fixtureCatalog):files[path.replace('/data/fixture/','')];return route.fulfill({status:body?200:404,contentType:'application/json',body:body??'not found'});});
  await missing.goto(h.base+'/steering-diff');await missing.getByRole('heading',{name:'Choose an intervention',exact:true}).waitFor();
  await missing.getByRole('combobox',{name:'Intervention',exact:true}).selectOption('joy_plus');
  await missing.locator('.page-footer').waitFor();
  check(await missing.getByRole('button',{name:'Full steered response',exact:true}).isDisabled(),'A replay-only export disables the own-response control');
  check((await missing.locator('.full-response-access').textContent()).includes('No independent response was exported'),'Unavailable own response is explicit, not a fixed-reference substitute');
  await missing.close();
  assert.deepEqual(errors,[]);check(requests.every(r=>r.method==='GET'&&new URL(r.url).origin===h.base),'Only local GETs; no model request or live Pi attachment');
  check(!requests.some(r=>/\/api\/pi|\/v1\/|chat\/completions/.test(r.url)),'No provider or conversation endpoints');
  await writeFile(root+'preview/checks/steering-response-browser.json',JSON.stringify({pass:true,checks,requests,errors},null,2)+'\n');
  console.log('FULL STEERED RESPONSE VERIFIED:',checks.length,'controls');
}finally{await h.close();}
