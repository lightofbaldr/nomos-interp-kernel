import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {harness,root,pause} from './harness.mjs';
const h=await harness(),checks=[],shots=[];
function check(value,label){assert.ok(value,label);checks.push(label);}
async function inViewport(page,selector,label){const box=await page.locator(selector).first().boundingBox(),size=page.viewportSize();check(Boolean(box&&box.width>0&&box.height>0&&box.x>=-1&&box.y>=-1&&box.x+box.width<=size.width+1&&box.y+box.height<=size.height+1),`${label} remains inside the viewport: ${JSON.stringify(box)}`);}
async function welcomeSettled(page){await page.locator('.welcome-reveal').waitFor();await page.waitForFunction(()=>{let node=document.querySelector('.welcome h1');if(!node)return false;for(;node;node=node.parentElement){const s=getComputedStyle(node);if(Number(s.opacity)<.99||s.visibility!=='visible')return false;}return true;});}
async function bounds(page,label){check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`No root overflow: ${label}`);const outside=await page.locator('button').evaluateAll(nodes=>nodes.filter(n=>{const b=n.getBoundingClientRect();return b.width&&b.height&&(b.left< -1||b.right>innerWidth+1);}).map(n=>n.getAttribute('aria-label')??n.textContent));check(outside.length===0,`Interactive controls stay in viewport: ${label}: ${JSON.stringify(outside)}`);}
try{
  for(const width of [320,375,414,768,1024,1280,1440]){
    const context=await h.browser.newContext({viewport:{width,height:width>=960?(width===1440?980:800):844},isMobile:width<500,hasTouch:width<500});const page=await context.newPage();h.observe(page);
    await page.goto(h.base,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);await bounds(page,width);
    check(await page.locator('.title-sparkles-effect svg').count()===4,`Local SparklesText renders four title sparkles at ${width}`);
    const mascot=page.locator('.conversation-stage>.chat-mascot');
    check(await mascot.evaluate(n=>n.complete&&n.naturalWidth>0&&n.getAttribute('alt')===''&&n.getAttribute('aria-hidden')==='true'&&getComputedStyle(n).pointerEvents==='none'),`Mascot loads as non-interactive decoration at ${width}`);
    check(await page.locator('.inspection-panel .chat-mascot,.composer-section .chat-mascot,.history-sidebar .chat-mascot').count()===0,`Mascot stays inside the conversation at ${width}`);
    check(await page.locator('svg[role="img"]:not([aria-hidden="true"]):not([aria-label])').count()===0,`Every exposed image icon has an accessible label at ${width}`);
    check(await page.getByLabel('Message Muse',{exact:true}).isVisible(),`Composer visible at ${width}`);
    await inViewport(page,'.muse-composer',`Composer at ${width}`);
    await inViewport(page,'.emotion-steering',`Steering Dock at ${width}`);
    const joy=page.getByRole('button',{name:'Joy steering preview',exact:true});
    const targetSizes=await page.locator('.emotion-toggle').evaluateAll(nodes=>nodes.map(n=>{const b=n.getBoundingClientRect();return [b.width,b.height];}));
    check(targetSizes.length===7&&targetSizes.every(([w,h])=>w>=59.9&&h>=59.9),`Seven labeled emotion toggles have at least 60px targets at ${width}`);
    check(await page.locator('.emotion-label').evaluateAll(nodes=>nodes.every(n=>{const label=n.getBoundingClientRect(),button=n.closest('button').getBoundingClientRect();return label.left>=button.left&&label.right<=button.right;})),`All emotion labels, including Desperation and Compassion, fit their buttons at ${width}`);
    if(width<=576){const rows=await page.locator('.emotion-toggle').evaluateAll(nodes=>new Set(nodes.map(n=>Math.round(n.getBoundingClientRect().top))).size);check(rows===2,`Seven choices wrap into two visible rows at ${width}`);}
    await joy.click();check(await joy.getAttribute('aria-pressed')==='true',`Touch/mouse selects a latched target at ${width}`);
    if(width<960){await joy.hover();await pause(200);check(await page.locator('.emotion-steering').getAttribute('data-motion')==='static'&&Math.abs((await joy.boundingBox()).width-60)<.5,`Narrow/touch Dock stays static at ${width}`);}
    const compassion=page.getByRole('button',{name:'Compassion steering preview',exact:true}),desperation=page.getByRole('button',{name:'Desperation steering preview',exact:true});
    await compassion.click();await desperation.click();check(await compassion.getAttribute('aria-pressed')==='true'&&await desperation.getAttribute('aria-pressed')==='true',`Both new emotions are selectable at ${width}`);
    await page.mouse.move(1,1);
    if(width>=960)await inViewport(page,'.inspection-footer',`Inspector timeline at ${width}`);
    check(await page.getByText('Scripted chat and illustrative probe data. No model is running.',{exact:true}).isVisible(),`Truthful source disclosure at ${width}`);
    if(width<960){
      check(await page.getByRole('dialog').count()===0,`Chat is primary, not covered by initial inspector at ${width}`);
      await page.getByRole('button',{name:'Show inspection',exact:true}).click();await page.getByRole('dialog').waitFor();await bounds(page,`${width} inspection`);
      await inViewport(page,'.mobile-inspection .inspection-footer',`Sheet timeline at ${width}`);
      check(await page.getByRole('heading',{name:'Behind the response',exact:true}).isVisible(),`Mobile inspection opens at ${width}`);
      await page.screenshot({path:root+`preview/inspection-${width}.png`});await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});check(await page.getByRole('dialog').count()===0,`Escape returns to chat at ${width}`);
      await page.getByRole('button',{name:'Open conversations',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'New conversation',exact:true}).click();await page.getByRole('heading',{name:/Space to think/}).waitFor();await bounds(page,`${width} welcome`);
      await welcomeSettled(page);check(true,`Welcome reveal is actually opaque before capture at ${width}`);
      check(await joy.getAttribute('aria-pressed')==='true',`Selection persists through mobile navigation at ${width}`);
    }
    // Capture the selected style after its short color transition and spring
    // have settled, not a mid-transition frame that looks like an off target.
    await pause(250);await page.waitForFunction(()=>[...document.querySelectorAll('.emotion-toggle')].every(n=>Math.abs(n.getBoundingClientRect().width-60)<.5));
    await page.screenshot({path:root+`preview/chat-${width}.png`});shots.push({width,image:`preview/chat-${width}.png`});await context.close();
  }
  const context=await h.browser.newContext({viewport:{width:1440,height:980},reducedMotion:'reduce'}),page=await context.newPage();h.observe(page);await page.goto(h.base,{waitUntil:'networkidle'});await page.locator('.history-sidebar').getByRole('button',{name:'New conversation',exact:true}).click();await page.getByRole('heading',{name:/Space to think/}).waitFor();
  check(await page.locator('.welcome').isVisible(),'Reduced-motion welcome remains visible');check(await page.locator('.letter-3d-swap-char-box-item').count()===0,'Reduced motion does not mount animated type');await page.screenshot({path:root+'preview/welcome-desktop.png'});
  check(await page.locator('.title-sparkles-effect').count()===0,'Reduced motion does not mount the sparkles interval/effect');check((await page.locator('.model-name').innerText()).includes('Muse Glimmer'),'Reduced-motion title remains readable');
  const quietJoy=page.getByRole('button',{name:'Joy steering preview',exact:true});await quietJoy.hover();await pause(200);check(await page.locator('.emotion-steering').getAttribute('data-motion')==='static'&&Math.abs((await quietJoy.boundingBox()).width-60)<.5,'Reduced motion disables Dock spring magnification, not just CSS transitions');
  // Observe normal-motion integration as a separate actual instance.
  await context.close();const motionContext=await h.browser.newContext({viewport:{width:1440,height:980}}),motionPage=await motionContext.newPage();h.observe(motionPage);await motionPage.goto(h.base,{waitUntil:'networkidle'});const effect=motionPage.locator('.motion-label').first();await motionPage.waitForFunction(()=>document.querySelector('.letter-3d-swap-char-box-item'));const before=await effect.locator('.letter-3d-swap-char-box-item').first().evaluate(n=>getComputedStyle(n).transform);await effect.hover();await motionPage.waitForFunction(before=>getComputedStyle(document.querySelector('.letter-3d-swap-char-box-item')).transform!==before,before);check(true,'Fancy letter swap actually animates on hover');
  await motionPage.locator('.history-sidebar').getByRole('button',{name:'New conversation',exact:true}).click();await welcomeSettled(motionPage);check(await motionPage.getByRole('heading',{name:/Space to think/}).isVisible(),'Magic welcome reveal completes');
  const motionJoy=motionPage.getByRole('button',{name:'Joy steering preview',exact:true}),dock=motionPage.locator('.emotion-steering');
  check(await dock.getAttribute('data-motion')==='magnify','Desktop pointer uses the actual local Magic Dock motion');
  const dockBox=await dock.boundingBox();await motionJoy.hover();await motionPage.waitForFunction(()=>document.querySelector('.emotion-toggle').getBoundingClientRect().width>64);check(true,'Dock proximity spring actually enlarges the hovered target');await bounds(motionPage,'magnified Dock');check(Math.abs((await dock.boundingBox()).height-dockBox.height)<.5,'Magnification does not shift the composer or chat height');
  await motionPage.getByRole('tooltip').waitFor();check((await motionPage.getByRole('tooltip').innerText()).includes('Preview only. No activations changed.'),'Pointer tooltip keeps selection distinct from application');check((await motionJoy.getAttribute('aria-describedby')).split(' ').includes(await motionPage.getByRole('tooltip').getAttribute('id')),'Tooltip is programmatically associated with the toggle');await motionPage.keyboard.press('Escape');await motionPage.getByRole('tooltip').waitFor({state:'hidden'});
  await motionPage.mouse.move(1,1);await motionPage.waitForFunction(()=>Math.abs(document.querySelector('.emotion-toggle').getBoundingClientRect().width-60)<.5);check(true,'Leaving the Dock restores resting size');
  await motionJoy.focus();await motionJoy.press('Tab');await motionPage.getByRole('tooltip').waitFor();check((await motionPage.getByRole('tooltip').innerText()).includes('Calm'),'Keyboard focus receives the emotion tooltip');await motionPage.keyboard.press('Escape');await motionPage.getByRole('tooltip').waitFor({state:'hidden'});
  const title=motionPage.locator('.title-sparkles'),stars=title.locator('svg');await motionPage.waitForFunction(()=>document.querySelectorAll('.title-sparkles-effect svg').length===4);
  const sparkleBefore=await stars.first().evaluate(n=>getComputedStyle(n).transform);await motionPage.waitForFunction(before=>getComputedStyle(document.querySelector('.title-sparkles-effect svg')).transform!==before,sparkleBefore);check(true,'Sparkles actually animate, not just render SVGs');
  const titleBox=await title.boundingBox();await motionPage.getByRole('button',{name:'Pause sparkles for Muse Glimmer on Nomos',exact:true}).click();check(await stars.count()===0,'Title click pauses and unmounts the looping effect');
  await motionPage.getByRole('button',{name:'Resume sparkles for Muse Glimmer on Nomos',exact:true}).press('Space');await motionPage.waitForFunction(()=>document.querySelectorAll('.title-sparkles-effect svg').length===4);check(await stars.count()===4,'Keyboard resumes the sparkles');const resumedBox=await title.boundingBox();check(Math.abs(titleBox.width-resumedBox.width)<1&&Math.abs(titleBox.height-resumedBox.height)<1,'Pausing does not shift the title layout');
  await motionJoy.click();await motionPage.emulateMedia({reducedMotion:'reduce'});await motionPage.locator('.title-sparkles-effect').waitFor({state:'hidden'});check(await stars.count()===0,'Changing reduced-motion preference removes the running effect');await motionJoy.hover();await pause(200);check(await dock.getAttribute('data-motion')==='static'&&Math.abs((await motionJoy.boundingBox()).width-60)<.5&&await motionJoy.getAttribute('aria-pressed')==='true','Changing motion preference keeps selected state but removes spring magnification');await motionPage.emulateMedia({reducedMotion:'no-preference'});await motionPage.waitForFunction(()=>document.querySelectorAll('.title-sparkles-effect svg').length===4);check(await stars.count()===4,'Returning to normal motion restores the effect without duplicate instances');check(await motionPage.locator('.emotion-toggle').count()===7&&await motionJoy.getAttribute('aria-pressed')==='true','Restored Dock has no duplicate instances or lost selection');
  check(h.errors.length===0,JSON.stringify(h.errors));check(h.external.length===0,'No remote asset/font/model requests');check(!h.requests.some(url=>/registry|\/v1\/chat|models\.dev/.test(url)),'No implicit registry or model calls');
  await writeFile(root+'preview/checks/ui.json',JSON.stringify({checkedAt:new Date().toISOString(),checks,shots,errors:h.errors,external:h.external,entry:h.entry},null,2));console.log(`MUSE UI PASSED: ${checks.length} observed checks.`);
}finally{await h.close();}
