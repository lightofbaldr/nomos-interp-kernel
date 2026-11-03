import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtemp,rm,readFile,writeFile,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {harness,root,pause} from './harness.mjs';
import {fakeKernel} from '../tests/live/fixture.ts';
import {fixture} from '../tests/workbench/recorded-fixture.ts';
const temp=await mkdtemp(path.join(tmpdir(),'recorded-browser-'));
let h,page,fake;
try{
  h=await harness(false,{GLIMMER_SESSION_DB:temp+'/unused.sqlite'});
  const context=await h.browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  page=await context.newPage();h.observe(page);page.setDefaultTimeout(15000);
  const methods=[];page.on('request',r=>methods.push([r.method(),r.url()]));
  await page.goto(h.base+'/workbench/recorded?conversation=138');
  await page.getByRole('article',{name:'Improved recorded rollout'}).waitFor();await page.evaluate(()=>document.fonts.ready);
  const response=await fetch(h.base+'/api/workbench-recorded',{headers:{Origin:h.base}});assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');
  const data=await response.json();assert.equal(data.results.base,1);assert.equal(data.results.improved,8);
  const base=page.getByRole('article',{name:'Base recorded rollout'}),improved=page.getByRole('article',{name:'Improved recorded rollout'});
  assert.match(await page.locator('.recording-label').innerText(),/RECORDED RUN.*No live generation/);
  assert.equal(await page.locator('.task-strip li').count(),10);
  for(const t of data.tasks)for(const side of ['base','improved'])assert.equal(await page.getByLabel(`${t.id} ${side} ${t[side].passed?'pass':t[side].rollout==='failed'?'failed rollout':'fail'}`,{exact:true}).count(),1);
  assert.equal(await page.getByLabel('Base saved event').locator('option').count(),6);assert.equal(await page.getByLabel('Improved saved event').locator('option').count(),15);
  // No autoplay: elapsed time alone cannot move the selection or the saved output.
  const before=await improved.getByLabel('Improved saved tool output').textContent();await pause(1500);assert.equal(await page.getByLabel('Improved saved event').inputValue(),'0');assert.equal(await improved.getByLabel('Improved saved tool output').textContent(),before);
  await base.getByRole('button',{name:'First source write',exact:true}).click();await improved.getByRole('button',{name:'First compiler feedback',exact:true}).click();
  assert.equal(await page.getByLabel('Base saved event').inputValue(),'4');assert.equal(await page.getByLabel('Improved saved event').inputValue(),'7');
  assert.equal(await page.getByLabel('Improved saved tool output').textContent(),data.improved.events[7].output);
  assert.match(await page.getByLabel('Improved saved tool output').innerText(),/implicitly copied/);
  assert.ok(await page.locator('.event-output').evaluateAll(els=>els.every(el=>getComputedStyle(el).color===getComputedStyle(el.closest('article')).color)),'Tool output must use the readable application ink, not the kit light-theme foreground');
  await page.locator('.lab-content').evaluate(el=>el.scrollTop=0);
  await page.screenshot({path:root+'preview/checks/workbench-recorded-desktop.png'});
  await improved.getByRole('button',{name:'Last self-check',exact:true}).click();assert.match(await page.getByLabel('Improved saved tool output').innerText(),/CHECK INCOMPLETE: compiled, but no code ran/);
  assert.equal(await page.getByLabel('Base saved event').inputValue(),'4','Sides must remain independent');
  for(const [label,arm] of [['Base',data.base],['Improved',data.improved]]){
    const panel=page.getByRole('article',{name:label+' recorded rollout'});
    // Every event remains reachable and exactly matches the saved output/arguments.
    for(let i=0;i<arm.events.length;i++){
      await page.getByLabel(label+' saved event').selectOption(String(i));assert.equal(await page.getByLabel(label+' saved tool output').textContent(),arm.events[i].output||'(Empty recorded output)');assert.deepEqual(JSON.parse(await page.getByLabel(label+' saved tool arguments').textContent()),arm.events[i].args);
    }
    await panel.locator('summary').filter({hasText:'Final submitted source'}).click();assert.equal(await panel.locator('pre.source').textContent(),arm.source);
    await panel.locator('summary').filter({hasText:'External grader'}).click();const gradeText=await panel.locator('.grader-details').innerText();assert.match(gradeText,new RegExp(`Compile · exit ${arm.grade.compile.returncode}`));
    if(label==='Improved')assert.match(gradeText,/Run · exit 0[\s\S]*ok/);else assert.match(gradeText,/Run: not attempted/);
    await panel.locator('summary').filter({hasText:'Identity & provenance'}).click();assert.ok((await panel.innerText()).includes(arm.harnessSha256));
  }
  await improved.getByRole('button',{name:'Last self-check',exact:true}).click();await page.locator('.comparison-grid').scrollIntoViewIfNeeded();
  await page.screenshot({path:root+'preview/checks/workbench-recorded-grader.png'});
  for(const file of data.changes){
    await page.getByLabel('Frozen file',{exact:true}).selectOption(file.path);
    if(file.improved)assert.equal(await page.getByLabel('improved harness file',{exact:true}).textContent(),file.improved.text||'(Empty file)');
    else assert.equal(await page.getByLabel('improved harness file',{exact:true}).count(),0);
  }
  await page.getByLabel('Frozen file',{exact:true}).selectOption('skills/mojo-verify/SKILL.md');
  assert.ok(!h.requests.some(u=>/^\/api\/(pi|nomos|meta|system)/.test(new URL(u).pathname)),'Direct recorded view must not attach Pi or probe the model');
  assert.ok(methods.every(([m])=>m==='GET'),'Recorded view emits no writes');
  await assert.rejects(stat(temp+'/unused.sqlite'),'A recorded view must not create a session database');
  assert.equal((await fetch(h.base+'/api/workbench-recorded')).status,403);
  assert.equal((await fetch(h.base+'/api/workbench-recorded',{headers:{Origin:'http://evil.test'}})).status,403);
  for(const query of ['?path=../../.env','?root=/','?id=base','?id=a&id=b'])assert.equal((await fetch(h.base+'/api/workbench-recorded'+query,{headers:{Origin:h.base}})).status,404);
  assert.equal((await fetch(h.base+'/api/workbench-recorded',{method:'POST',headers:{Origin:h.base}})).status,405);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.ok(await page.locator('.lab-content').evaluate(el=>el.scrollWidth<=el.clientWidth),'No hidden horizontal overflow in the actual scroller');
  await page.locator('.lab-content').evaluate(el=>el.scrollTop=0);await page.screenshot({path:root+'preview/checks/workbench-recorded-mobile.png'});
  await improved.scrollIntoViewIfNeeded();await page.screenshot({path:root+'preview/checks/workbench-recorded-mobile-event.png'});
  // Hostile content remains text. Fixture is explicitly a browser test response,
  // never a selectable production recording or an executable harness import.
  const fixtureData=await fixture();let malicious;try{malicious=await fixtureData.load();}finally{await fixtureData.close();}
  malicious.improved.events[0].output='<img src="https://example.invalid/leak" onerror="window.RECORDED_RAN=true">';
  await page.route('**/api/workbench-recorded',r=>r.fulfill({json:malicious}));await page.reload();await page.getByRole('article',{name:'Improved recorded rollout'}).waitFor();
  assert.equal(await page.getByLabel('Improved saved tool output').innerText(),malicious.improved.events[0].output);assert.equal(await page.evaluate(()=>window.RECORDED_RAN),undefined);assert.equal(await page.evaluate(()=>window.BAD),undefined);assert.equal(await page.locator('.recorded-workbench img').count(),0);assert.equal(await page.locator('.recorded-workbench script').count(),0);
  // Missing evidence is explicit, never silently replaced by another recording.
  await page.unroute('**/api/workbench-recorded');await page.route('**/api/workbench-recorded',r=>r.fulfill({json:{error:'Recorded comparison unavailable or its identity changed. No data was substituted.'},status:404}));
  const expectedErrorCount=h.errors.length;await page.reload();await page.getByRole('heading',{name:'Recording unavailable'}).waitFor();assert.equal(await page.locator('.comparison-grid').count(),0);h.errors.splice(expectedErrorCount);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);await h.close();h=null;
  // SPA navigation from an already attached conversation must unmount its bridge,
  // and a query parameter must not reattach it. Only the fake kernel is enabled.
  fake=await fakeKernel();h=await harness(false,{NOMOS_API_BASE_URL:fake.base,GLIMMER_SESSION_DB:fake.root+'/recorded-nav.sqlite',NOMOS_READOUT_REGISTRY:fake.registry,NOMOS_TOKENIZER_JSON:fake.tokenizerPath,NOMOS_TOKENIZER_SHA256:fake.tokenizerPin});
  page=await h.browser.newPage({viewport:{width:1440,height:900}});h.observe(page);await page.goto(h.base+'/workbench');await page.getByRole('button',{name:'Save profile',exact:true}).waitFor();
  const history=async()=>{const r=await fetch(h.base+'/api/pi?history=1',{headers:{Origin:h.base}});return(await r.json()).conversations;};
  const saved=await history();h.requests.length=0;
  await page.getByRole('link',{name:'View recorded Base | Improved | Changes →',exact:true}).click();await page.getByRole('article',{name:'Improved recorded rollout'}).waitFor();await pause(800);
  assert.equal(await page.locator('.live-conversation').count(),0);assert.ok(!h.requests.some(u=>new URL(u).pathname.startsWith('/api/pi')));assert.deepEqual(await history(),saved);assert.equal(fake.requests.length,0);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
}catch(e){if(page&&!page.isClosed()){console.error((await page.locator('body').innerText()).slice(-7000));await page.screenshot({path:root+'preview/checks/workbench-recorded-failure.png'});}throw e;}
finally{await h?.close();if(fake){await fake.close();await rm(fake.root,{recursive:true,force:true});}await rm(temp,{recursive:true,force:true});}
const followups=spawnSync(process.execPath,['--test','scripts/workbench-recorded-followups-browser.mjs'],{cwd:root,stdio:'inherit'});
assert.equal(followups.status,0,'Recorded-view scaffold and deferred-history regressions');
await writeFile(root+'preview/checks/workbench-recorded-browser.json',JSON.stringify({pass:true,realModelCalls:0,recordedTools:[6,15],tasks:10,noSessionCreated:true,spaDetach:true,scaffoldNotes:4,deferredHistory:true,desktop:[1440,900],mobile:[390,844]},null,2));
console.log('RECORDED WORKBENCH BROWSER VERIFIED');
