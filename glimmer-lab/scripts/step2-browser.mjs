import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {harness,root} from './harness.mjs';
import {answerWindows} from '../src/lib/workspace/step2.ts';
import {candidates} from '../src/lib/workspace/model.ts';
// Real saved captures; local adapter only. No live provider or paid API.
const temp=await mkdtemp(path.join(tmpdir(),'step2-browser-'));
let h,page;
try{
  h=await harness(false,{GLIMMER_SESSION_DB:temp+'/sessions.sqlite'});
  page=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});h.observe(page);page.setDefaultTimeout(15000);
  const api=async query=>{const r=await fetch(h.base+'/api/recordings'+query,{headers:{Origin:h.base}});assert.equal(r.status,200);return r.json();};
  const catalog=await api('');assert.equal(catalog.step2.length,48);assert.equal(catalog.recordings.length,2);
  await page.goto(h.base+'/workspace?recording=sietti-base');await page.locator('.word-token').first().waitFor();
  assert.equal(h.requests.filter(x=>new URL(x).pathname==='/api/recordings'&&!new URL(x).search).length,0,'Legacy/live paths must not require the external study catalog');
  h.requests.length=0;
  await page.goto(h.base+'/workspace?recording=step2-kind-e39fe23efc76');await page.locator('.word-token').first().waitFor();
  await page.evaluate(()=>document.fonts.ready);
  assert.equal(await page.getByLabel('Study item').locator('option').count(),8);assert.equal(await page.getByLabel('Study setup').locator('option').count(),6);
  assert.equal(h.requests.filter(x=>new URL(x).pathname==='/api/recordings'&&new URL(x).searchParams.has('id')).length,1,'Only the selected capture was requested');
  const turn=await api('?id=step2-kind-e39fe23efc76'),win=answerWindows(turn.records,turn.step2);
  assert.equal(await page.locator('.registered-token').count(),win.registered.length);
  assert.deepEqual(await page.locator('.registered-token').evaluateAll(xs=>xs.map(x=>Number(x.dataset.pos))),win.registered);
  await page.getByRole('button',{name:'Task response starts here →',exact:true}).click();
  assert.equal(await page.locator('.word-token[aria-pressed=true]').getAttribute('data-pos'),String(win.taskStart));
  assert.equal(await page.locator('.word-token[aria-pressed=true]').getAttribute('class').then(x=>x.includes('task-start')),true);
  assert.match(await page.locator('.study-windows').innerText(),/post hoc, not a decision time/);
  await page.getByRole('button',{name:'Reasoning',exact:true}).click();assert.equal(await page.locator('.registered-token').count(),0);
  await page.getByRole('button',{name:/Registered window/}).click();assert.equal(await page.locator('.word-token[aria-pressed=true]').getAttribute('data-pos'),String(win.registered[0]));
  const selected=turn.records.find(r=>r.pos===win.registered[0]);
  for(const layer of turn.layers){
    const article=page.getByRole('article',{name:`Workspace layer ${layer}`,exact:true});
    assert.deepEqual(await article.locator('.candidate-rank').allTextContents(),candidates(turn,selected,layer).filter(c=>!c.hiddenReason).map(c=>String(c.rank)));
  }
  await page.screenshot({path:root+'preview/checks/step2-desktop.png'});
  await page.getByLabel('Word-like only').uncheck();assert.equal(await page.locator('.depth-candidates li').count(),64);
  for(const layer of turn.layers){
    const raw=candidates(turn,selected,layer);
    assert.deepEqual(await page.getByRole('article',{name:`Workspace layer ${layer}`,exact:true}).locator('.depth-candidates li').allTextContents(),raw.map(c=>`${c.rank}${c.label??`#${c.id} · unknown label`}${c.score.toFixed(2)}`));
  }
  await page.getByRole('button',{name:'Setup & provenance ↓',exact:true}).click();
  assert.equal(await page.locator('.study-provenance pre').nth(0).innerText(),turn.step2.system);assert.equal(await page.locator('.study-provenance pre').nth(1).innerText(),turn.step2.opener);
  const popup=page.waitForEvent('popup');await page.getByRole('link',{name:'Separate precheck G1 evidence ↗',exact:true}).click();const proof=await popup;await proof.waitForLoadState();assert.match(await proof.locator('body').innerText(),/G1_repeat/);await proof.close();
  await page.locator('.study-provenance').screenshot({path:root+'preview/checks/step2-provenance.png'});
  await page.getByLabel('Study item').selectOption('e7e6accc4bcd');await page.waitForURL(u=>u.searchParams.get('recording')==='step2-kind-e7e6accc4bcd');await page.locator('.word-token').first().waitFor();
  await page.getByLabel('Study setup').selectOption('fact_checker');await page.locator('.label-warning').waitFor();assert.match(await page.locator('.label-warning').innerText(),/character 764.*opening refusal/);
  await page.screenshot({path:root+'preview/checks/step2-lens-exception.png'});
  // Shortest supplied answer: actual coverage, never a fabricated 64 rows.
  await page.getByLabel('Study item').selectOption('204c565a4012');await page.locator('.word-token').first().waitFor();
  await page.getByLabel('Study setup').selectOption('calibrated');await page.getByRole('button',{name:'Registered window · 15/64 answer tokens',exact:true}).waitFor();assert.equal(await page.locator('.registered-token').count(),15);
  assert.equal(await page.getByText('Not measured',{exact:true}).count(),2);
  await page.getByLabel('Study setup').selectOption('kind');await page.getByRole('button',{name:'Registered window · 64/64 answer tokens',exact:true}).waitFor();
  await page.getByLabel('Study item').selectOption('ecfce718efca');await page.getByText(/Answer token index 65 \(zero-based\)/).waitFor();
  await page.getByRole('button',{name:'Task response starts here →',exact:true}).click();assert.equal(await page.locator('.word-token[aria-pressed=true].registered-token').count(),0,'Task start may lie outside the registered first 64');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:root+'preview/checks/step2-mobile.png',fullPage:true});
  await page.getByRole('button',{name:'Task response starts here →',exact:true}).click();
  await page.locator('.workspace-inspection').scrollIntoViewIfNeeded();await page.screenshot({path:root+'preview/checks/step2-mobile-inspection.png'});
  await page.getByLabel('Workspace source').selectOption('sietti-afraid');await page.locator('.workspace-source').getByText('afraid +0.5 · L24',{exact:true}).waitFor();assert.equal(await page.getByLabel('Study setup').count(),0);
  assert.equal((await fetch(h.base+'/api/recordings?id=../../.env',{headers:{Origin:h.base}})).status,404);
  assert.equal((await fetch(h.base+'/api/recordings?evidence=../../.env',{headers:{Origin:h.base}})).status,404);
  assert.equal((await fetch(h.base+'/api/recordings?id=sietti-base&id=sietti-afraid',{headers:{Origin:h.base}})).status,404);
  assert.equal((await fetch(h.base+'/api/recordings',{headers:{Origin:'http://evil.test'}})).status,403);
  assert.equal((await fetch(h.base+'/api/recordings')).status,403);
  assert.equal((await fetch(h.base+'/api/recordings',{method:'POST',headers:{Origin:h.base}})).status,405);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  assert.ok(!h.requests.some(u=>/8091|8092|8093|\/api\/nomos|\/api\/pi\/annotate/.test(u)));
  await writeFile(root+'preview/checks/step2-browser.json',JSON.stringify({pass:true,modelCalls:0,recordings:48,desktop:[1440,900],mobile:[390,844],external:h.external},null,2));
  console.log('STEP2 BROWSER VERIFIED');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:root+'preview/checks/step2-failure.png'});}throw e;}
finally{await h?.close();await rm(temp,{recursive:true,force:true});}
