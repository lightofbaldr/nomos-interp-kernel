import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {once} from 'node:events';
import {readFile,readdir,mkdir} from 'node:fs/promises';
import {chromium} from 'playwright';
import {homedir} from 'node:os';
export const root=new URL('../',import.meta.url).pathname;
export const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export async function harness(live=false,extraEnv={}){
  await mkdir(root+'preview/checks',{recursive:true});
  let base='http://127.0.0.1:5226',server,log='';
  if(!live){const s=createServer();s.listen(0,'127.0.0.1');await once(s,'listening');const port=s.address().port;await new Promise(resolve=>s.close(resolve));base=`http://127.0.0.1:${port}`;server=spawn(process.execPath,['build/index.js'],{cwd:root,env:{...process.env,NOMOS_API_BASE_URL:'',...extraEnv,HOST:'127.0.0.1',PORT:String(port),ORIGIN:base},stdio:['ignore','pipe','pipe']});server.stdout.on('data',x=>log+=x);server.stderr.on('data',x=>log+=x);}
  let browser;
  async function close(){await browser?.close();if(server&&server.exitCode===null){server.kill('SIGTERM');await Promise.race([once(server,'exit'),pause(3000)]);if(server.exitCode===null){server.kill('SIGKILL');await once(server,'exit');}}}
  try{
    let ready=false;for(let n=0;n<100;n++){if(server?.exitCode!==null&&server?.exitCode!==undefined)throw Error(log);try{if((await fetch(base)).ok){ready=true;break;}}catch{}await pause(100);}assert.ok(ready,'Current production server must start');
    // The framework bootstrap (start.*) can stay byte-identical across app
    // changes. app.* references the application's current route chunks.
    const entry=(await readdir(root+'build/client/_app/immutable/entry')).find(n=>/^app\..*\.js$/.test(n));assert.ok(entry,'Current application entry must exist');const html=await (await fetch(base)).text();assert.ok(html.includes(entry),'Server must expose the current application manifest, not stale code');assert.deepEqual(Buffer.from(await (await fetch(`${base}/_app/immutable/entry/${entry}`)).arrayBuffer()),await readFile(root+'build/client/_app/immutable/entry/'+entry));
    browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE??`${homedir()}/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`,headless:true,args:['--disable-gpu']});
    const errors=[],external=[],requests=[];
    function observe(page){page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),location:m.location()});});page.on('response',r=>{if(r.status()>=400)errors.push({url:r.url(),status:r.status()});});page.on('request',r=>{requests.push(r.url());if(/^https?:/.test(r.url())&&new URL(r.url()).origin!==base)external.push(r.url());});}
    return {base,browser,entry,errors,external,requests,observe,close};
  }catch(e){await close();throw e;}
}
