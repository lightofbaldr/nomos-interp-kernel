import assert from 'node:assert/strict';
import {readFile,rm,writeFile} from 'node:fs/promises';
import {harness} from './harness.mjs';
import {fileKernel} from '../tests/files/fixture.ts';
import {until} from '../tests/live/fixture.ts';
const f=await fileKernel();let h,page;
try{
  h=await harness(false,{NOMOS_API_BASE_URL:f.base,GLIMMER_SESSION_DB:f.root+'/file-ui.sqlite',NOMOS_READOUT_REGISTRY:f.registry,NOMOS_TOKENIZER_JSON:f.tokenizerPath,NOMOS_TOKENIZER_SHA256:f.tokenizerPin});
  page=await h.browser.newPage({viewport:{width:1500,height:1100}});h.observe(page);page.setDefaultTimeout(15000);
  await page.goto(h.base);await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Profile',exact:true}).click();
  const a=await page.getByLabel('Pi conversation').inputValue();
  await page.getByLabel('Profile instructions').waitFor();assert.equal(await page.getByLabel('Enable tool write',{exact:true}).isEnabled(),false);
  await page.getByRole('button',{name:'Check tool isolation',exact:true}).click();await page.getByTestId('tools-availability').filter({hasText:'Tools available'}).waitFor();
  for(const name of ['read','write'])await page.getByLabel(`Enable tool ${name}`,{exact:true}).check();
  await page.getByRole('button',{name:'Save profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  f.plan([{name:'write',args:{path:'main.mojo',content:'def main():\n    print("hello from sandbox")\n'}},{name:'read',args:{path:'main.mojo'}},null]);
  await page.getByLabel('Message Muse',{exact:true}).fill('Write and read a file.');await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByText('File task complete.',{exact:true}).waitFor();assert.equal(f.requests.length,3);assert.ok(f.requests.every(b=>b.tools.length===2));
  const write=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/write Completed/})});
  await write.getByRole('button').click();await write.getByLabel('write result').waitFor();assert.match(await write.innerText(),/written.*true/s);
  const read=page.getByTestId('file-tool-call').filter({has:page.getByRole('button',{name:/read Completed/})});
  await read.getByRole('button').click();assert.match(await read.getByLabel('read result').innerText(),/hello from sandbox/);
  const original=await readFile(f.root+`/file-ui.sqlite.workspaces/workspaces/${a}/main.mojo`,'utf8');assert.match(original,/hello from sandbox/);
  await page.getByRole('button',{name:/Profile r\d+ · inline skills · 2 tools/}).last().click();await page.getByRole('dialog').waitFor();assert.match(await page.getByRole('dialog').innerText(),/Tools: read, write/);await page.getByRole('button',{name:'Close profile',exact:true}).click();
  await page.getByLabel('Enable tool write',{exact:true}).uncheck();await page.getByRole('button',{name:'Save profile',exact:true}).click();await page.getByText('Profile applied.',{exact:true}).waitFor();
  await page.reload();await page.getByText('Pi connected',{exact:true}).waitFor();await page.getByRole('tab',{name:'Profile',exact:true}).click();await page.getByLabel('Enable tool read',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Enable tool read',{exact:true}).isChecked(),true);assert.equal(await page.getByLabel('Enable tool write',{exact:true}).isChecked(),false);
  await page.locator('.live-inspector').evaluate(el=>el.scrollTop=0);await page.screenshot({path:'preview/checks/file-tools-desktop.png',fullPage:true});
  await page.getByRole('button',{name:'New session',exact:true}).click();await until(async()=>await page.getByLabel('Pi conversation').inputValue()!==a);
  await until(async()=>!await page.getByLabel('Enable tool read',{exact:true}).isChecked());
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Show inspection',exact:true}).click();await page.getByRole('tab',{name:'Profile',exact:true}).click();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'preview/checks/file-tools-mobile.png',fullPage:true});
  assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);console.log('FILE TOOL BROWSER PASS');
}catch(e){await writeFile('preview/checks/file-tools-failure.json',JSON.stringify({error:String(e),stack:e?.stack,browserErrors:h?.errors},null,2));if(page)await page.screenshot({path:'preview/checks/file-tools-failure.png',fullPage:true});throw e;}
finally{await h?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
