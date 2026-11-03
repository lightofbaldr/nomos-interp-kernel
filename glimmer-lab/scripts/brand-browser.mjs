// Exercise the real local SparklesText component. The provider and DB are
// isolated fixtures; no model request, paid call or live conversation is used.
import assert from 'node:assert/strict';
import {rm,writeFile} from 'node:fs/promises';
import {harness,root} from './harness.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
const f=await fakeKernel(),checks=[];let h;
const check=(value,label)=>{assert.ok(value,label);checks.push(label);};
async function stars(page,count){await page.waitForFunction(n=>document.querySelectorAll('.brand-sparkles svg').length===n,count);}
async function fits(page){
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow');
  check(await page.locator('.lab-brand-link').evaluateAll(nodes=>nodes.every(n=>{
    const r=n.getBoundingClientRect();return !r.width||(r.left>=0&&r.right<=innerWidth&&n.scrollWidth<=n.clientWidth+1);
  })),'Wordmark text stays within its link and viewport');
}
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/brand.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  const ssr=await h.browser.newPage({javaScriptEnabled:false});
  await ssr.goto(h.base+'/');
  check((await ssr.locator('.brand-word').innerText()).trim()==='glimmer','SSR supplies readable logo text without JavaScript');
  check(await ssr.locator('.brand-sparkles').count()===0,'SSR does not generate random sparkles');
  await ssr.close();
  const context=await h.browser.newContext({viewport:{width:1440,height:900},reducedMotion:'no-preference'});
  const page=await context.newPage();h.observe(page);page.setDefaultTimeout(12000);
  await page.goto(h.base+'/workbench/recorded');await stars(page,4);
  const link=page.getByRole('link',{name:'Glimmer Lab home',exact:true});
  check(await link.getAttribute('href')==='/','Logo remains a home link');
  check((await link.innerText()).replace(/\s/g,'')==='glimmerLAB','Wordmark is real glimmer LAB text');
  check(await link.locator('img,canvas,button').count()===0,'No image logo, canvas or nested interactive control');
  check(await link.locator('.brand-word').getAttribute('aria-hidden')==='true','Decorative sparkles are excluded from the home link accessible name');
  const transform=await link.locator('.brand-sparkles svg').first().evaluate(n=>getComputedStyle(n).transform);
  await page.waitForFunction(before=>getComputedStyle(document.querySelector('.brand-sparkles svg')).transform!==before,transform);
  check(true,'Sparkles actually animate');
  const box=await link.boundingBox();
  await page.getByRole('button',{name:'Pause logo sparkles',exact:true}).click();await stars(page,0);
  const paused=await link.boundingBox();check(Math.abs(paused.width-box.width)<1&&Math.abs(paused.height-box.height)<1,'Pause does not shift the wordmark');
  check(new URL(page.url()).pathname==='/workbench/recorded','Pause does not navigate');
  await page.getByRole('button',{name:'Resume logo sparkles',exact:true}).press('Space');await stars(page,4);
  await page.emulateMedia({reducedMotion:'reduce'});await stars(page,0);
  check(await page.locator('.brand-motion-toggle').count()===0,'Reduced motion unmounts both the animation and its unnecessary toggle');
  check((await link.innerText()).replace(/\s/g,'')==='glimmerLAB','Reduced-motion wordmark remains readable');
  await page.emulateMedia({reducedMotion:'no-preference'});await stars(page,4);
  // Simulate the browser visibility signal; the effect is unmounted, not merely
  // hidden by CSS. SparklesText itself owns and cleans up its interval.
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});await stars(page,0);
  await page.evaluate(()=>{delete document.visibilityState;document.dispatchEvent(new Event('visibilitychange'));});await stars(page,4);
  check(true,'Hidden-page signal stops and restores the animation');
  await link.press('Enter');await page.waitForURL(u=>u.pathname==='/');
  await until(async()=>await page.getByLabel('Message Muse',{exact:true}).isEnabled());await stars(page,4);
  check(true,'Keyboard activation still navigates home');
  await page.evaluate(()=>document.fonts.ready);await fits(page);
  await page.waitForFunction(()=>[...document.querySelectorAll('.brand-sparkles svg')].some(n=>Number(getComputedStyle(n).opacity)>.2));
  await page.screenshot({path:root+'preview/checks/brand-sparkles-desktop.png'});
  await page.setViewportSize({width:800,height:900});await fits(page);
  await page.setViewportSize({width:390,height:844});await stars(page,0);
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();await stars(page,4);await fits(page);
  const navigation=page.getByRole('dialog',{name:'Lab navigation',exact:true});
  check(await navigation.locator('.brand-sparkles svg').count()===4,'Only the visible mobile logo animates, not the hidden desktop instance');
  await page.waitForFunction(()=>[...document.querySelectorAll('.brand-sparkles svg')].some(n=>Number(getComputedStyle(n).opacity)>.2));
  await page.screenshot({path:root+'preview/checks/brand-sparkles-mobile.png'});
  await page.keyboard.press('Escape');await navigation.waitFor({state:'hidden'});await stars(page,0);
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();await stars(page,4);
  await navigation.getByRole('button',{name:'Pause logo sparkles',exact:true}).click();await stars(page,0);await page.keyboard.press('Escape');await navigation.waitFor({state:'hidden'});
  await page.setViewportSize({width:1440,height:900});
  await page.getByRole('button',{name:'Resume logo sparkles',exact:true}).waitFor();
  check(await page.locator('.brand-sparkles').count()===0,'Pause is shared by desktop and mobile instances');
  await page.getByRole('button',{name:'Resume logo sparkles',exact:true}).press('Space');await stars(page,4);
  await page.getByRole('link',{name:'Steering diff',exact:true}).click();await page.locator('.page-footer').waitFor();await stars(page,0);
  await page.getByRole('link',{name:'Back to chat',exact:true}).click();await stars(page,4);
  check(true,'Navigation unmount/remount leaves exactly one visible sparkle instance');
  await context.close();
  const quiet=await h.browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'}),q=await quiet.newPage();h.observe(q);
  await q.goto(h.base+'/workbench/recorded');await q.locator('.changes-count').waitFor();
  check(await q.locator('.brand-sparkles,.brand-motion-toggle').count()===0,'Initially reduced-motion hydration stays static');
  await quiet.close();
  check(f.requests.length===0,'No model requests');assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  await writeFile(root+'preview/checks/brand-sparkles.json',JSON.stringify({pass:true,entry:h.entry,checks,modelRequests:f.requests.length},null,2)+'\n');
  console.log('SPARKLES WORDMARK VERIFIED:',checks.length,'controls');
}finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
