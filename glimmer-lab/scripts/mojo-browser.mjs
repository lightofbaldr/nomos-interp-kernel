import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {harness} from './harness.mjs';
import {fileKernel} from '../tests/files/fixture.ts';
const f=await fileKernel();let h;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/mojo-ui.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin,GLIMMER_MOJO_PREFIX:'/home/adam/Projects/nomos-interp-kernel/.pixi/envs/default'});
  const page=await h.browser.newPage({viewport:{width:1500,height:1100}});h.observe(page);page.setDefaultTimeout(15000);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Profile',exact:true}).click();
  await page.getByLabel('Enable tool mojo',{exact:true}).waitFor();assert.equal(await page.getByLabel('Enable tool mojo',{exact:true}).isEnabled(),false);
  await page.getByRole('button',{name:'Check Mojo toolchain',exact:true}).click();await page.getByTestId('mojo-availability').filter({hasText:'Mojo available'}).waitFor();
  for(const name of ['write','mojo'])await page.getByLabel(`Enable tool ${name}`,{exact:true}).check();
  await page.getByRole('button',{name:'Save profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  f.plan([{name:'write',args:{path:'main.mojo',content:'def main():\n    print("browser-mojo-ok")\n'}},{name:'mojo',args:{path:'main.mojo'}},null]);
  await page.getByLabel('Message Muse',{exact:true}).fill('Create and run a Mojo program.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByText('File task complete.',{exact:true}).waitFor();
  const tool=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/mojo Completed/})});
  await tool.getByRole('button').click();assert.match(await tool.getByLabel('mojo result').innerText(),/browser-mojo-ok/);
  assert.match(await tool.innerText(),/not a correctness grade/);
  const stdout=tool.getByLabel('Mojo run stdout',{exact:true});assert.equal((await stdout.innerText()).trim(),'browser-mojo-ok');
  await stdout.scrollIntoViewIfNeeded();
  const contrast=await stdout.evaluate(el=>{
    // Resolve CSS colors (including this app's OKLCH tokens) to sRGB, as contrast.mjs does.
    const style=getComputedStyle(el),canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
    const rgb=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3);};
    const lum=color=>rgb(color).map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4).reduce((n,v,i)=>n+v*[0.2126,0.7152,0.0722][i],0);
    const ratio=(a,b)=>(Math.max(lum(a),lum(b))+0.05)/(Math.min(lum(a),lum(b))+0.05);
    if(ratio('black','black')!==1||Math.abs(ratio('white','black')-21)>1e-10)throw Error('Contrast controls failed');
    return ratio(style.color,style.backgroundColor);
  });assert.ok(contrast>=4.5,`Tool output contrast ${contrast}`);
  await page.screenshot({path:'preview/checks/mojo-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Show inspection',exact:true}).click();await page.getByRole('tab',{name:'Profile',exact:true}).click();
  await page.getByRole('heading',{name:/Mojo build/}).scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'preview/checks/mojo-mobile.png',fullPage:true});assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  await page.setViewportSize({width:1500,height:1100});
  f.plan([{name:'write',args:{path:'bad.mojo',content:'not valid mojo source\n'}},{name:'mojo',args:{path:'bad.mojo'}},null]);
  await page.getByLabel('Message Muse',{exact:true}).fill('Try the invalid source.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  const failed=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/mojo Error/})});
  await failed.getByRole('button').click();assert.match(await failed.innerText(),/Run · not started/);
  assert.ok((await failed.getByLabel('Mojo compile stderr',{exact:true}).innerText()).length>0);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  console.log('MOJO BROWSER PASS');
}finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
