import assert from 'node:assert/strict';
import {harness} from './harness.mjs';
import {fakeKernel,until} from '../tests/live/fixture.ts';
const f=await fakeKernel();let h;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/controls.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  const page=await h.browser.newPage();h.observe(page);page.setDefaultTimeout(10000);
  await page.goto(h.base+'/preview');await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Controls',exact:true}).click();
  const session=page.getByLabel('Pi conversation'),a=await session.inputValue(),strength=page.getByLabel('Signed steering strength'),workspace=page.getByLabel('Workspace top-16',{exact:true});
  await page.getByLabel('Emotion capture layer').selectOption('20');await workspace.check();await page.getByLabel('Workspace capture layer').selectOption('36');await page.getByRole('button',{name:'Apply readout settings',exact:true}).click();
  await page.getByLabel('Steering direction').selectOption('joyful');await strength.fill('-0.5');await page.getByRole('button',{name:'Apply steering',exact:true}).click();
  await page.getByTestId('steer-setting').filter({hasText:'joyful -0.5 at L24'}).waitFor();
  await page.getByRole('button',{name:'New session',exact:true}).click();await until(async()=>await session.inputValue()!==a);await page.getByText('Pi connected',{exact:true}).waitFor();
  const b=await session.inputValue();await page.getByTestId('steer-setting').filter({hasText:'steering off'}).waitFor();
  await until(async()=>await strength.inputValue()==='0');assert.equal(await workspace.isChecked(),false);
  assert.equal(await page.getByLabel('Emotion capture layer').inputValue(),'29');assert.equal(await page.getByLabel('Workspace capture layer').inputValue(),'44');
  assert.equal(await page.getByLabel('Steering direction').inputValue(),'afraid');
  await session.selectOption(a);await page.getByTestId('steer-setting').filter({hasText:'joyful -0.5 at L24'}).waitFor();
  await until(async()=>await strength.inputValue()==='-0.5');assert.equal(await workspace.isChecked(),true);assert.equal(await page.getByLabel('Emotion capture layer').inputValue(),'20');assert.equal(await page.getByLabel('Workspace capture layer').inputValue(),'36');assert.equal(await page.getByLabel('Steering direction').inputValue(),'joyful');
  // A same-session bridge update must not keep overwriting an unsaved edit.
  await strength.fill('0.9');await page.getByRole('tab',{name:'Status',exact:true}).click();await page.getByRole('tab',{name:'Controls',exact:true}).click();await page.getByRole('button',{name:'Reconnect Pi bridge',exact:true}).click();await page.getByText('Pi connected',{exact:true}).waitFor();assert.equal(await strength.inputValue(),'0.9');
  await session.selectOption(b);await page.getByTestId('steer-setting').filter({hasText:'steering off'}).waitFor();await until(async()=>await strength.inputValue()==='0');
  await session.selectOption(a);await page.getByTestId('steer-setting').filter({hasText:'joyful -0.5 at L24'}).waitFor();await until(async()=>await strength.inputValue()==='-0.5');
  await page.reload();await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Controls',exact:true}).click();assert.equal(await strength.inputValue(),'-0.5');assert.equal(await workspace.isChecked(),true);
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);assert.equal(f.requests.length,0,'No generation is needed to restore forms');console.log('SAVED SESSION CONTROLS VERIFIED');
}finally{await h?.close();await f.close();}
