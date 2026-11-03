import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {harness,root,pause} from './harness.mjs';

// Current production build, real pinned recording, browser-intercepted history
// and System data. No Pi attachment, real session DB, host probes or model calls.
test('recorded-view review regressions',async t=>{
  const temp=await mkdtemp(path.join(tmpdir(),'recorded-followups-'));
  const h=await harness(false,{GLIMMER_SESSION_DB:temp+'/unused.sqlite'});
  t.after(async()=>{await h.close();await rm(temp,{recursive:true,force:true});});
  const pageFor=async()=>{const p=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});h.observe(p);p.setDefaultTimeout(5000);return p;};
  await t.test('four scaffold notes stay inspectable but do not inflate executor additions',async()=>{
    const p=await pageFor();try{
      await p.goto(h.base+'/workbench/recorded');await p.locator('.changes-count').waitFor();
      assert.match(await p.locator('.changes-count').innerText(),/^4 added · 2 changed · 3 unchanged/);
      assert.match(await p.locator('.changes-count').innerText(),/plus 4 scaffold notes/);
      for(const file of ['skills/README.md','scripts/README.md','extensions/README.md','sub_agents/README.md']){
        await p.getByLabel('Frozen file',{exact:true}).selectOption(file);
        assert.match(await p.locator('.scaffold-note').innerText(),/Improver scaffold, not loaded by Glimmer/);
        assert.match(await p.locator('.scaffold-note').innerText(),/Excluded from the executor.*identity/);
        assert.match(await p.getByLabel('Frozen file',{exact:true}).locator('option:checked').innerText(),/scaffold note/);
        assert.ok((await p.getByLabel('improved harness file',{exact:true}).textContent()).length>0);
      }
      await p.locator('.harness-changes').scrollIntoViewIfNeeded();await p.screenshot({path:root+'preview/checks/workbench-scaffold-notes.png'});
      await p.getByLabel('Frozen file',{exact:true}).selectOption('skills/mojo-verify/SKILL.md');assert.equal(await p.locator('.scaffold-note').count(),0);
    }finally{await p.close();}
  });
  for(const [name,target] of [['System','/system'],['Global Workspace','/workspace']])await t.test(`direct recorded → ${name} loads history once without mounting LiveChat`,async()=>{
    const p=await pageFor();try{
      let reads=0;const calls=[];p.on('request',r=>calls.push(new URL(r.url()).pathname+new URL(r.url()).search));
      await p.route('**/api/pi?history=1',async route=>{reads++;await pause(100);await route.fulfill({json:{conversations:[{id:'41',title:'Existing saved conversation',busy:false}]}});});
      await p.route('**/api/system',r=>r.fulfill({json:{at:null,endpoint:null,host:null,health:null,healthError:null,cache:{stats:null,at:null,message:'Fixture only'}}}));
      await p.goto(h.base+'/workbench/recorded');await p.locator('.changes-count').waitFor();assert.equal(reads,0);
      await p.getByRole('navigation',{name:'Lab pages'}).getByRole('link',{name,exact:true}).click();await p.waitForURL(u=>u.pathname===target);
      await p.locator('.lab-history').getByRole('link',{name:'Existing saved conversation',exact:true}).waitFor();
      assert.equal(reads,1);assert.equal(await p.locator('.live-conversation').count(),0);
      // Once filled, returning through the recorded route does not refetch it.
      await p.getByRole('link',{name:'Recorded harness',exact:true}).click();await p.locator('.changes-count').waitFor();
      await p.getByRole('navigation',{name:'Lab pages'}).getByRole('link',{name,exact:true}).click();await p.waitForURL(u=>u.pathname===target);await pause(200);
      assert.equal(reads,1);assert.equal(await p.locator('.live-conversation').count(),0);
      assert.deepEqual(calls.filter(u=>u.startsWith('/api/pi')),['/api/pi?history=1']);
    }finally{await p.close();}
  });
  await t.test('failed first history read is visible and does not cause a request loop',async()=>{
    const p=await pageFor();try{
      let reads=0;await p.route('**/api/pi?history=1',r=>{reads++;return r.fulfill({json:{error:'Fixture history unavailable.'}});});
      await p.goto(h.base+'/workbench/recorded');await p.locator('.changes-count').waitFor();assert.equal(reads,0);
      await p.getByRole('navigation',{name:'Lab pages'}).getByRole('link',{name:'Global Workspace',exact:true}).click();
      await p.locator('.lab-history').getByRole('status').waitFor();assert.match(await p.locator('.lab-history').innerText(),/Fixture history unavailable/);await pause(300);assert.equal(reads,1);
      assert.equal(await p.locator('.live-conversation').count(),0);
    }finally{await p.close();}
  });
  await assert.rejects(stat(temp+'/unused.sqlite'));assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
});
