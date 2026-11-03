import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {mkdir,writeFile,readFile,rm,symlink} from 'node:fs/promises';
import {harness} from './harness.mjs';
import {agentKernel} from '../tests/agent/fixture.ts';
import {until} from '../tests/live/fixture.ts';
import {waitForProfileSettled} from './profile-settled.mjs';
const f=await agentKernel();let h,page;
const database=f.root+'/agent-ui.sqlite',prefix=(process.env.GLIMMER_MOJO_PREFIX??path.join(os.homedir(),'Projects/nomos-interp-kernel/.pixi/envs/default'));
const get=url=>fetch(url,{headers:{Origin:h.base}});
try{
  await mkdir(f.root+'/imports/improved/skills/verify',{recursive:true});
  await writeFile(f.root+'/imports/improved/systemprompt.md','SYNTHETIC_IMPORTED_PROFILE: verify the program.');
  await writeFile(f.root+'/imports/improved/skills/verify/SKILL.md','---\nname: verify\ndescription: Check before claiming success\n---\nSynthetic fixture only.');
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:database,GLIMMER_ENABLE_SANDBOX_SHELL:'',GLIMMER_MOJO_PREFIX:prefix,NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin,GLIMMER_HARNESS_IMPORT_ROOTS:JSON.stringify([f.root+'/imports'])});
  page=await h.browser.newPage({viewport:{width:1440,height:900}});h.observe(page);page.setDefaultTimeout(20000);
  await page.goto(h.base+'/');
  const mode=page.getByRole('switch',{name:'Agent mode',exact:true});await mode.waitFor();await until(async()=>await mode.isEnabled());
  assert.equal(await mode.isChecked(),false);assert.equal(f.requests.length,0);
  await page.getByRole('button',{name:'Harness & files',exact:true}).click();
  await page.getByText('No workspace yet. A sandboxed tool can create one; viewing does not.',{exact:true}).waitFor();
  const history=await (await get(h.base+'/api/pi?history=1')).json(),id=history.conversations[0].id;
  await assert.rejects(readFile(database+`.workspaces/workspaces/${id}/main.mojo`));
  // UI recovery control: a crash can leave an unowned durable ledger even
  // when Pi itself is idle, so the composer has no Stop button. Real ledger
  // recovery/refusal is separately exercised in tests/agent/budget.test.ts.
  let recovered=false;
  const sawStop=request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/pi'&&request.postDataJSON()?.action==='abort')recovered=true;};
  page.on('request',sawStop);
  await page.route(`**/api/pi/budget?conversation=${id}`,async route=>{
    // Playwright's API fetch does not supply browser Sec-Fetch metadata. Use
    // the isolated server's explicit Origin for this synthetic recovery stub.
    const response=await route.fetch({headers:{...route.request().headers(),Origin:h.base}});assert.equal(response.status(),200);
    if(recovered){await route.fulfill({response});return;}
    const data=await response.json();data.owned=false;data.run={status:'running',modelRequests:1,toolCalls:0};
    await route.fulfill({response,json:data});
  });
  await page.reload();await page.getByRole('button',{name:'Harness & files',exact:true}).click();
  await page.getByRole('button',{name:'Stop saved run',exact:true}).click();
  await page.getByText('Saved run stopped. Inspect its files before starting again.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Stop saved run',exact:true}).count(),0);await page.unroute(`**/api/pi/budget?conversation=${id}`);page.off('request',sawStop);
  await mode.check();await page.getByText('· sandboxed · no network',{exact:true}).waitFor();
  const profile=await (await get(h.base+`/api/pi/profile?conversation=${id}`)).json();
  assert.deepEqual(profile.profile.active.content.tools,['read','write','edit','ls','find','grep','mojo']);
  assert.equal(f.requests.length,0,'Switch probes are CPU sandbox checks, not model calls');
  f.plan([{name:'write',args:{path:'main.mojo',content:'def main():\n    print("agent-ui-ok")\n'}},{name:'mojo',args:{path:'main.mojo'}},null]);
  await page.getByLabel('Message Muse',{exact:true}).fill('Create and run a short Mojo program.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByText('Synthetic agent task complete.',{exact:true}).waitFor();await waitForProfileSettled(h.base,id);
  await page.getByTestId('workspace-entry').filter({hasText:'main.mojo'}).click();
  await page.getByTestId('workspace-preview').filter({hasText:'agent-ui-ok'}).waitFor();
  assert.equal(f.requests.length,3);assert.ok(f.requests.every(r=>r.max_tokens===8192&&r.nomos.readout.workspace.k===16&&r.nomos.readout.emotion.layers.includes(29)));
  assert.ok((await page.locator('.conversation-stage').boundingBox()).height>150,'Expanded agent pane must leave the conversation visible');
  assert.ok((await page.getByRole('region',{name:'Agent workspace readouts'}).innerText()).includes('L44'));
  const mojo=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/mojo Completed/})});await mojo.getByRole('button').click();
  assert.equal((await mojo.getByLabel('Mojo run stdout',{exact:true}).innerText()).trim(),'agent-ui-ok');
  await page.screenshot({path:'preview/checks/agent-mode-desktop.png',fullPage:true});
  // The reader displays escaped source, never markup, and never follows links.
  const work=database+`.workspaces/workspaces/${id}`;
  await writeFile(f.root+'/decoy-secret','SYNTHETIC_AGENT_CANARY',{mode:0o600});await symlink(f.root+'/decoy-secret',work+'/decoy');
  await writeFile(work+'/note.html','<script>window.AGENT_EXECUTED=true</script>',{mode:0o600});
  await page.getByTestId('workspace-refresh').click();await page.getByTestId('workspace-entry').filter({hasText:'note.html'}).click();
  await page.getByTestId('workspace-preview').filter({hasText:'<script>'}).waitFor();assert.equal(await page.evaluate(()=>window.AGENT_EXECUTED),undefined);
  assert.equal(await page.getByTestId('workspace-entry').filter({hasText:'decoy'}).count(),0);
  const listing=await (await get(h.base+`/api/pi/files?conversation=${id}`)).json();
  for(const query of [`conversation=${id}&path=../../decoy-secret`,`conversation=${id}&snapshot=${listing.snapshot}&entry=../../decoy-secret`,`conversation=${id}&conversation=999`])assert.equal((await get(h.base+'/api/pi/files?'+query)).status,404);
  assert.equal((await fetch(h.base+`/api/pi/files?conversation=${id}`,{headers:{Origin:'https://evil.test'}})).status,403);
  // Import and choose a harness entirely from Chat, using the existing importer.
  await page.getByRole('button',{name:'Configure harness & budget',exact:true}).click();
  await page.getByRole('button',{name:'Import / edit harness',exact:true}).click();await page.getByRole('dialog').waitFor();
  assert.equal(await page.getByRole('button',{name:'Check shell isolation',exact:true}).isDisabled(),true);
  assert.equal(await page.getByLabel('Enable tool bash',{exact:true}).isDisabled(),true);
  await page.getByLabel('Harness relative path').fill('improved');await page.getByRole('button',{name:'Preview import and diff',exact:true}).click();await page.getByTestId('import-preview').waitFor();
  await page.getByRole('button',{name:'Apply previewed text profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Back to Agent mode',exact:true}).click();await until(async()=>!(await mode.isChecked()));
  assert.match(await page.getByTestId('agent-mode').innerText(),/Imported.*inline skills/);
  await mode.check();await until(async()=>await mode.isChecked());
  await page.getByLabel('Agent harness',{exact:true}).selectOption('base');await page.getByRole('button',{name:'Apply harness',exact:true}).click();
  await until(async()=>{const p=await (await get(h.base+`/api/pi/profile?conversation=${id}`)).json();return p.profile.active.content.instructions==='You are a helpful AI assistant.\nReasoning strength: high.';});
  assert.equal(await mode.isChecked(),true,'Base instructions retain the current tool selection');
  const importedOption=page.getByLabel('Agent harness',{exact:true}).locator('option').filter({hasText:'Imported'}).first();
  await page.getByLabel('Agent harness',{exact:true}).selectOption(await importedOption.getAttribute('value'));
  await page.getByRole('button',{name:'Fork with this harness',exact:true}).click();
  await until(async()=>(await (await get(h.base+'/api/pi?history=1')).json()).conversations.length===2);
  await page.getByRole('button',{name:'Harness & files',exact:true}).click();
  await page.getByText('No workspace yet. A sandboxed tool can create one; viewing does not.',{exact:true}).waitFor();
  assert.equal(f.requests.length,3,'All harness and inspection actions are model-free');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'preview/checks/agent-mode-mobile.png',fullPage:true});
  assert.deepEqual(f.errors,[]);assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
  console.log('AGENT MODE BROWSER VERIFIED');
}catch(e){if(page){console.error((await page.locator('body').innerText()).slice(-9000));await page.screenshot({path:'preview/checks/agent-mode-failure.png',fullPage:true});}throw e;}
finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
