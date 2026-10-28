import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {harness,root} from './harness.mjs';

const h=await harness(),states=[];
try{
  const page=await h.browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  h.observe(page);
  await page.goto(h.base,{waitUntil:'networkidle'});
  async function inspect(name){
    const result=await page.evaluate(()=>{
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
      const ctx=canvas.getContext('2d');
      function rgba(color){ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((v,i)=>i===3?v/255:v);}
      function over(a,b){return [...a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3])),1];}
      function luminance(a){return a.slice(0,3).map(x=>{const v=x/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((total,v,i)=>total+v*[.2126,.7152,.0722][i],0);}
      function ratio(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
      function background(el){const chain=[];for(let n=el;n;n=n.parentElement)chain.unshift(n);let bg=[0,0,0,1];for(const n of chain)bg=over(rgba(getComputedStyle(n).backgroundColor),bg);return bg;}
      const samples=[];
      for(const el of document.querySelectorAll('body *')){
        if(el.closest('[aria-hidden="true"],.sr-only,button:disabled,input:disabled,textarea:disabled'))continue;
        if(![...el.childNodes].some(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim()))continue;
        const box=el.getBoundingClientRect(),css=getComputedStyle(el);
        if(!box.width||!box.height||css.visibility!=='visible'||css.display==='none')continue;
        const bg=background(el),fg=over(rgba(css.color),bg);
        const large=parseFloat(css.fontSize)>=24||(parseFloat(css.fontSize)>=18&&Number(css.fontWeight)>=700);
        const r=ratio(fg,bg);
        samples.push({text:el.textContent.trim().slice(0,70),class:el.className,ratio:Number(r.toFixed(3)),threshold:large?3:4.5});
        const mascot=el.closest('.conversation-stage')?.querySelector('.chat-mascot');
        if(mascot){
          // Conservative image-aware check: even a fully white, opaque asset
          // pixel at maximum CSS opacity must leave the text readable. This
          // also overestimates the effect behind opaque message bubbles.
          const opacity=Number(getComputedStyle(mascot).opacity),worstBg=over([255,255,255,opacity],bg),worstFg=over(rgba(css.color),worstBg);
          samples.push({text:el.textContent.trim().slice(0,70),class:el.className+' (white mascot pixel bound)',ratio:Number(ratio(worstFg,worstBg).toFixed(3)),threshold:large?3:4.5});
        }
      }
      const css=getComputedStyle(document.documentElement),color=n=>rgba(css.getPropertyValue('--color-'+n));
      const tokenPairs=['paper','paper-2','paper-3','inspector','sidebar'].flatMap(bg=>['ink','ink-2','muted','faint'].map(fg=>({foreground:fg,background:bg,ratio:Number(ratio(color(fg),color(bg)).toFixed(3))})));
      const focusPairs=['paper','paper-2','paper-3','inspector','sidebar','accent'].map(bg=>({background:bg,ratio:Number(ratio(color('focus'),color(bg)).toFixed(3))}));
      const accentInk=ratio(color('accent-ink'),color('accent'));
      return {samples:samples.length,worst:samples.sort((a,b)=>a.ratio-b.ratio).slice(0,8),failures:samples.filter(s=>s.ratio<s.threshold),tokenPairs,focusPairs,accentInk};
    });
    assert.ok(result.samples>20,`${name}: positive control must find real text`);
    assert.deepEqual(result.failures,[],`${name}: rendered text contrast`);
    assert.ok(result.tokenPairs.every(x=>x.ratio>=4.5),'Foreground tokens clear 4.5:1 on all neutral surfaces');
    // The focus outline is outside the filled button, against the page/panel.
    assert.ok(result.focusPairs.filter(x=>x.background!=='accent').every(x=>x.ratio>=3),'Focus outlines clear 3:1 against surrounding neutral surfaces');
    assert.ok(result.accentInk>=4.5,'Filled blue button uses contrasting accent ink');
    states.push({name,...result});
  }
  await inspect('conversation + overview');
  for(const name of ['Joy','Anger','Desperation','Compassion'])await page.getByRole('button',{name:`${name} steering preview`,exact:true}).click();await inspect('selected emotion Dock');
  await page.getByRole('button',{name:'Calm steering preview',exact:true}).focus();await page.getByRole('tooltip').waitFor();await inspect('emotion tooltip');await page.keyboard.press('Escape');await page.getByRole('tooltip').waitFor({state:'hidden'});
  await page.getByRole('tab',{name:'Layers',exact:true}).click();await inspect('layer matrix');
  await page.getByRole('tab',{name:'Trace',exact:true}).click();await inspect('trace metadata');
  await page.getByRole('button',{name:'Demo replay',exact:true}).first().click();await inspect('source dialog');
  await page.getByRole('dialog').getByRole('button',{name:/Live kernel/}).click();await page.getByRole('tab',{name:'Signals',exact:true}).click();await inspect('disconnected');
  await page.getByRole('button',{name:'About interpretability readouts',exact:true}).click();await inspect('research explanation');
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Live / disconnected',exact:true}).first().click();await page.getByRole('dialog').getByRole('button',{name:/Demo replay/}).click();
  await page.locator('.history-sidebar').getByRole('button',{name:'New conversation',exact:true}).click();await inspect('welcome');
  assert.deepEqual(h.errors,[]);
  await writeFile(root+'preview/checks/contrast.json',JSON.stringify({checkedAt:new Date().toISOString(),states,scope:'Computed sRGB canvas conversion, inherited backgrounds and text; conservative full-white mascot-pixel bound at the rendered CSS opacity. Disabled controls are exempt; not a full WCAG/axe audit. Icon geometry, pseudo-background compositing and transient animation opacity are not certified.'},null,2));
  console.log(`MUSE CONTRAST PASSED: ${states.length} rendered states.`);
}finally{await h.close();}
