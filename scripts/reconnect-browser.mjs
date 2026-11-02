import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {harness} from './harness.mjs';
import {fakeKernel} from '../tests/live/fixture.ts';
const f=await fakeKernel();let h;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/reconnect.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  const page=await h.browser.newPage({viewport:{width:1440,height:900}});h.observe(page);page.setDefaultTimeout(12000);
  const posts=[];page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
  const stream='**/api/pi?conversation=*';
  // A valid empty stream simulates a dropped bridge, not a provider failure.
  // It must not submit/retry a model request or move sampling back into Chat.
  await page.route(stream,route=>route.fulfill({status:200,contentType:'text/event-stream',body:''}));
  await page.goto(h.base+'/');const reconnect=page.getByRole('button',{name:'Reconnect disconnected Pi bridge',exact:true});await reconnect.waitFor();
  assert.equal(await page.getByRole('switch',{name:"Glimmer's sampling",exact:true}).isVisible(),false);
  await page.screenshot({path:'preview/checks/bridge-disconnected.png',fullPage:true});
  const writes=posts.length;
  await page.unroute(stream);await reconnect.click();await reconnect.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Steering & readouts',exact:true}).click();
  await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByText('Durable Pi session',{exact:true}).waitFor();
  assert.equal(await page.getByRole('switch',{name:"Glimmer's sampling",exact:true}).isVisible(),true);
  assert.equal(f.requests.length,0);assert.equal(posts.length,writes,'Reconnect only reads the saved session; no new submit, retry or setting write');
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);console.log('BRIDGE RECOVERY BROWSER VERIFIED');
}finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
