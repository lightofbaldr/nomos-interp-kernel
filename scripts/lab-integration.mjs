import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {harness,root} from './harness.mjs';
const h=await harness(),checks=[];
const check=(v,label)=>{assert.ok(v,label);checks.push(label);};
const style=page=>page.evaluate(()=>{
  const result={};for(const q of ['.response-text','.chat-header','.emotion-steering','.model-name']){
    const el=document.querySelector(q);if(!el)throw Error(q);const s=getComputedStyle(el);
    result[q]=Object.fromEntries(['color','fontFamily','fontSize','lineHeight','whiteSpace','backgroundColor','display'].map(k=>[k,s[k]]));
  }return result;
});
try{
  const page=await h.browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});h.observe(page);
  await page.goto(h.base,{waitUntil:'networkidle'});const before=await style(page);
  check(!h.requests.some(u=>u.includes('/data/')),'Chat does not load capture data');
  await page.evaluate(()=>window.__labNavigation=42);
  await page.getByRole('link',{name:'Steering diff · recorded',exact:true}).click();
  await page.locator('.page-footer').waitFor();
  check(await page.evaluate(()=>window.__labNavigation===42),'Native client navigation, not a second app');
  check((await page.title()).includes('Steering diff'),'Route title follows explorer');
  check(await page.locator('.steering-diff-root .lab-shell').count()===1,'Recorded explorer has its own CSS root');
  await page.getByRole('button',{name:'Source & gates'}).click();await page.getByRole('dialog').waitFor();
  check(await page.getByRole('dialog').evaluate(el=>Boolean(el.closest('.steering-diff-root'))),'Portalled dialog retains its scoped theme');
  await page.keyboard.press('Escape');
  await page.getByRole('link',{name:'Back to chat',exact:true}).click();await page.locator('.chat-header').waitFor();
  assert.deepEqual(await style(page),before);checks.push('Chat computed styles unchanged after explorer and portal');
  check(await page.evaluate(()=>window.__labNavigation===42),'Back link uses native navigation');
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Open conversations',exact:true}).click();
  await page.getByRole('dialog').getByRole('link',{name:'Steering diff · recorded',exact:true}).click();await page.locator('.page-footer').waitFor();
  check(await page.getByRole('dialog').count()===0,'Mobile drawer is removed on navigation');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile route has no root overflow');
  await fs.mkdir(root+'evidence',{recursive:true});await page.screenshot({path:root+'evidence/lab-integrated-mobile.png',fullPage:true});
  await page.setViewportSize({width:1512,height:1120});await page.screenshot({path:root+'evidence/lab-integrated-desktop.png',fullPage:true});
  // Server allowlist and method boundary, without passing hostile text to a shell.
  for(const u of ['/data/nope.json','/data/dataset-1/package.json','/data/dataset-1/%252e%252e/package.json']){
    const r=await fetch(h.base+u);check(r.status===404,'Unknown local file refused');check(!(await r.text()).includes('/home/adam'),'Refusal reveals no private path');
  }
  const write=await fetch(h.base+'/data/catalog.json',{method:'POST'});check(write.status===405,'Adapter accepts no writes');
  check(!h.requests.some(u=>/\/api\/nomos\/|\/v1\/|8091/.test(u)),'Recorded navigation makes no model requests');
  check(h.errors.length===0,JSON.stringify(h.errors));check(h.external.length===0,'No third-party browser requests');
}finally{await h.close();}
const absent=await harness(false,{GLIMMER_STEERDIFF_DATA_DIR:'/nonexistent-glimmer-lab-data'});
try{
  const page=await absent.browser.newPage();await page.goto(absent.base+'/steering-diff');
  await page.getByRole('alert').waitFor();check((await page.getByRole('alert').innerText()).includes('unavailable'),'Missing local bundles are explicit');
  check(await page.locator('.candidate-row,.response-card').count()===0,'Missing data never displays substitute results');
}finally{await absent.close();}
await fs.writeFile(root+'evidence/integration.json',JSON.stringify({pass:true,checks},null,2));
console.log('LAB INTEGRATION VERIFIED:',checks.length,'controls');
