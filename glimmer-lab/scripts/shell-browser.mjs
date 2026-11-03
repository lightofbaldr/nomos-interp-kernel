import assert from 'node:assert/strict';
import {rm} from 'node:fs/promises';
import {harness} from './harness.mjs';
import {agentKernel} from '../tests/agent/fixture.ts';
import {until} from '../tests/live/fixture.ts';
import {waitForProfileSettled} from './profile-settled.mjs';
const f=await agentKernel();let h,page;
try{
  // Isolated synthetic instance only. The production operator flag stays absent.
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/shell-ui.sqlite',GLIMMER_ENABLE_SANDBOX_SHELL:'1',GLIMMER_MOJO_PREFIX:'/home/adam/Projects/nomos-interp-kernel/.pixi/envs/default',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  page=await h.browser.newPage({viewport:{width:1440,height:900}});h.observe(page);page.setDefaultTimeout(20000);
  await page.goto(h.base+'/');const mode=page.getByRole('switch',{name:'Agent mode',exact:true});await until(async()=>await mode.isEnabled());await mode.check();
  // Activation opens the workspace after the checks finish.
  await page.getByRole('button',{name:'Hide agent workspace',exact:true}).waitFor();
  await page.getByRole('button',{name:'Configure harness & budget',exact:true}).click();await page.getByRole('button',{name:'Import / edit harness',exact:true}).click();
  const bash=page.getByLabel('Enable tool bash',{exact:true});assert.equal(await bash.isChecked(),false);assert.equal(await bash.isDisabled(),true);
  await page.getByRole('button',{name:'Check shell isolation',exact:true}).click();await until(async()=>await bash.isEnabled());await bash.check();await page.getByRole('button',{name:'Save profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Back to Agent mode',exact:true}).click();assert.equal(f.requests.length,0);
  f.plan([{name:'bash',args:{command:"printf 'synthetic shell workspace' > shell-note.txt; printf '<script>window.SHELL_EXECUTED=true</script>'"}},null]);
  await page.getByLabel('Message Muse',{exact:true}).fill('Synthetic shell demonstration.');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic agent task complete.',{exact:true}).waitFor();
  const id=(await (await fetch(h.base+'/api/pi?history=1',{headers:{Origin:h.base}})).json()).conversations[0].id;await waitForProfileSettled(h.base,id);
  const tool=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/bash Completed/})});await tool.getByRole('button').click();
  assert.equal(await tool.getByLabel('Shell stdout',{exact:true}).innerText(),'<script>window.SHELL_EXECUTED=true</script>');assert.equal(await page.evaluate(()=>window.SHELL_EXECUTED),undefined);
  await page.getByTestId('workspace-entry').filter({hasText:'shell-note.txt'}).click();await page.getByTestId('workspace-preview').filter({hasText:'synthetic shell workspace'}).waitFor();
  assert.ok((await page.getByRole('region',{name:'Agent workspace readouts'}).innerText()).includes('L44'));assert.equal(f.requests.length,2);
  await page.screenshot({path:'preview/checks/agent-shell-synthetic.png',fullPage:true});
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);assert.deepEqual(f.errors,[]);console.log('OPT-IN SANDBOXED SHELL BROWSER VERIFIED');
}finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
