import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {harness} from './harness.mjs';
import {DEFAULT_STEERING_DATA} from '../src/lib/server/steering-data.ts';
const root=process.env.GLIMMER_STEERDIFF_DATA_DIR??DEFAULT_STEERING_DATA;
const h=await harness(false);let page;
try{
  page=await h.browser.newPage({viewport:{width:1536,height:1050}});page.setDefaultTimeout(12000);h.observe(page);
  await page.goto(h.base+'/steering-diff');await page.locator('.page-footer').waitFor();
  const sampledRequests=()=>h.requests.filter(u=>/\/data\/(sampled-sietti|sampled-tathuga|voice-tathuga)\//.test(u)&&!u.endsWith('/index.json'));
  assert.equal(sampledRequests().length,0,'curated comparisons are not prefetched');
  const scenario=page.getByRole('combobox',{name:'Scenario',exact:true}),arm=page.getByRole('combobox',{name:'Intervention',exact:true}),sample=page.getByRole('combobox',{name:'Paired sample',exact:true});
  const expected=[];
  async function ready(id,prompt,name,k){
    const rel=`${prompt}/${name}.s${k}.free.json`,raw=JSON.parse(await fs.readFile(path.join(root,id,rel),'utf8'));
    await page.locator('.page-footer').filter({hasText:rel}).waitFor();expected.push(`/data/${id}/${rel}`);
    assert.equal(await sample.inputValue(),String(k));assert.match(await page.getByRole('region',{name:'Sample pairing and interpretation'}).innerText(),new RegExp(`Sample s${k}.*paired seed ${raw.comparison.baseline.arm.seed}`));
    for(const side of ['baseline','steered'])assert.match(await page.getByTestId(`${side}-supplied-label`).innerText(),new RegExp(`${raw.labels.by}’s label: ${raw.labels[side]?.label??'Not supplied'}`));
    assert.match(await page.locator('.mode-explanation:visible').innerText(),/Only the prompt is prefilled/);assert.doesNotMatch(await page.locator('#responses .section-description').innerText(),/recorded thinking was prefilled/);
    return raw;
  }
  await scenario.selectOption('sampled-sietti:SIETTI-reason');await arm.selectOption('fear_0.5');
  assert.equal(sampledRequests().length,0,'sample and exported mode must be selected explicitly');
  await page.getByRole('tab',{name:'Free generation',exact:true}).click();await page.getByRole('heading',{name:'Choose a paired sample',exact:true}).waitFor();
  assert.equal(sampledRequests().length,0);await sample.selectOption('0');await ready('sampled-sietti','SIETTI-reason','fear_0.5',0);
  await sample.selectOption('1');const fear=await ready('sampled-sietti','SIETTI-reason','fear_0.5',1);
  await page.getByRole('region',{name:'Sample pairing and interpretation'}).getByRole('button',{name:/Random control/}).click();const random=await ready('sampled-sietti','SIETTI-reason','rand_-0.5',1);
  assert.equal(fear.comparison.baseline.arm.seed,random.comparison.baseline.arm.seed);assert.match(await page.locator('.sample-context').innerText(),/Random pushes can change outcomes too/);
  await page.screenshot({path:'preview/checks/sampled-sietti.png',fullPage:true});await page.screenshot({path:'preview/checks/sampled-sietti-viewport.png'});
  await arm.selectOption('base_repeat');await ready('sampled-sietti','SIETTI-reason','base_repeat',0);assert.equal(await sample.locator('option').count(),2);
  await arm.selectOption('zero');await ready('sampled-sietti','SIETTI-reason','zero',0);
  await arm.selectOption('fear_0.5');await ready('sampled-sietti','SIETTI-reason','fear_0.5',1);
  await scenario.selectOption('sampled-tathuga:TATHUGA-reason');await page.getByRole('heading',{name:'Choose a paired sample',exact:true}).waitFor();await sample.selectOption('4');await ready('sampled-tathuga','TATHUGA-reason','fear_0.5',4);
  await scenario.selectOption('voice-tathuga:TATHUGA-reason');await page.getByRole('heading',{name:'Choose an intervention',exact:true}).waitFor();
  await arm.selectOption('joy_0.5');await ready('voice-tathuga','TATHUGA-reason','joy_0.5',7);
  await page.getByTestId('workspace-disabled').waitFor();assert.equal(await page.locator('.candidate-panel').count(),0);assert.equal(await page.locator('.layer-picker').count(),0);
  assert.equal(await arm.locator('option').count(),7,'only six actually exported voice/control arms plus placeholder');
  await arm.selectOption('rand_0.5');await ready('voice-tathuga','TATHUGA-reason','rand_0.5',3);
  await page.getByRole('button',{name:'Source & gates',exact:true}).click();await page.getByRole('dialog').waitFor();await page.getByRole('button',{name:'Close source details',exact:true}).click();
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'preview/checks/sampled-voice-mobile.png',fullPage:true});
  assert.deepEqual(sampledRequests().map(u=>new URL(u).pathname),expected,'only the selected sample/arm files are fetched, no bulk prefetch');
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  console.log('SAMPLED BUNDLES BROWSER VERIFIED');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:'preview/checks/sampled-failure.png',fullPage:true});}throw e;}finally{await h.close();}
