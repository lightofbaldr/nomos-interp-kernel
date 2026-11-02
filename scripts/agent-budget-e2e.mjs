// Explicit opt-in client. No serve launcher, paid provider, live lab or retries.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {createRuntime} from '../src/lib/server/pi-runtime.ts';
import {createFileBroker} from '../src/lib/server/file-broker.ts';
import {assetCatalog} from '../src/lib/server/pi-assets.ts';
import {assertSupportedRuntime} from './runtime.mjs';
import {agentKernel} from '../tests/agent/fixture.ts';
assertSupportedRuntime();
const [mode,out,manifest]=process.argv.slice(2),live=mode==='--live';
assert.ok(['--fixture','--live'].includes(mode)&&out&&manifest&&process.argv.length===5,'Use --fixture|--live NEW_ABSOLUTE_DIR ABSOLUTE_COUNTER_MANIFEST. Live requires Prime’s exclusive slot.');
assert.ok(path.isAbsolute(out)&&path.isAbsolute(manifest));
const upstream=live?'http://127.0.0.1:8091':null,python='/home/adam/Projects/open-webui/.venv/bin/python';
const prefix=process.env.GLIMMER_MOJO_PREFIX;assert.ok(prefix&&path.isAbsolute(prefix),'Set the approved compiler prefix.');
const sha=b=>createHash('sha256').update(b).digest('hex');
const save=(name,obj)=>writeFile(path.join(out,name),JSON.stringify(obj,null,2)+'\n',{flag:'wx',mode:0o600});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const ss=(kind)=>execFileSync('ss',kind==='listen'?['-Hltpn','sport = :8091']:['-Htnp','state','established','( sport = :8091 or dport = :8091 )'],{encoding:'utf8'}).trim();
const noClients=()=>assert.equal(ss('clients'),'','Another :8091 client exists; no model request allowed.');
function onlyOurClients(){
  const rows=ss('clients').split('\n').filter(Boolean).map(line=>({line,fields:line.trim().split(/\s+/)}));
  const clients=rows.filter(r=>r.fields[3]==='127.0.0.1:8091');
  assert.ok(clients.every(r=>r.line.includes(`pid=${process.pid},`)),'Another client reached the serve during the test');
  assert.equal(rows.length,2*clients.length);for(const r of rows.filter(r=>r.fields[2]==='127.0.0.1:8091'))assert.ok(clients.some(c=>c.fields[2]===r.fields[3]));
}
const source='def main():\n    print("glimmer-agent-budget-ok")\n';
const prompt=`Use the sandboxed tools, not a prose-only answer. With write, create main.mojo containing exactly:\n${source}\nThen invoke mojo on main.mojo once. Report the actual compile/run exit codes and stdout. Do not claim a run unless its tool result exists. This is only a tool-path smoke check.`;
async function count(bytes){
  return new Promise((resolve,reject)=>{
    const child=spawn(python,['-I','-B','scripts/agent-prompt-count.py','--manifest',manifest],{env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},stdio:['pipe','pipe','pipe']});
    let stdout='',stderr='',size=0;const timer=setTimeout(()=>child.kill('SIGKILL'),55000);
    for(const [stream,name] of [[child.stdout,'stdout'],[child.stderr,'stderr']])stream.on('data',b=>{size+=b.length;if(size>8192)child.kill('SIGKILL');else if(name==='stdout')stdout+=b;else stderr+=b;});
    child.stdin.on('error',()=>{});child.once('error',reject);child.once('close',code=>{clearTimeout(timer);try{const c=JSON.parse(stdout);assert.equal(code,0,stderr||'Counter refused');assert.equal(c.fits,true);assert.equal(c.payload_sha256,sha(bytes));assert.equal(c.capacity,32768);assert.equal(c.reserve_tokens,2048);resolve(c);}catch(e){reject(e);}});child.stdin.end(bytes);
  });
}
await mkdir(out,{mode:0o700});
let fixture,runtime,server,initialListener,healthBefore,healthAfter,activeId,failed=null,turn=0,requests=0,inFlight=false;
const measures=[],results=[];let proxyFailure=null,turnRequests=0,turnTools=0;
try{
  if(live){noClients();initialListener=ss('listen');assert.ok(initialListener);}
  else fixture=await agentKernel();
  const base=upstream??fixture.base;
  const health=async()=>{const r=await fetch(base+'/health',{headers:{Connection:'close'},redirect:'error',signal:AbortSignal.timeout(5000)});assert.equal(r.status,200);const h=await r.json();assert.equal(h.ready,true);assert.equal(h.readout?.supported,true);return h;};
  healthBefore=await health();await pause(100);if(live)noClients();
  const pin='c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d';
  const assets=live?await assetCatalog('/home/adam/nomos_data/readout-assets/registry.json',healthBefore,pin):fixture.assets;
  assert.equal(assets.supported,true);
  await save('launch.json',{mode,node:process.versions,commit:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),
    status:execFileSync('git',['status','--short'],{encoding:'utf8'}),script:sha(await readFile(new URL(import.meta.url))),counter:sha(await readFile('scripts/agent-prompt-count.py')),
    manifest:sha(await readFile(manifest)),capacityEvidence:live?'Prime 1714: running laptop engine and serve both NOMOS_MAX_SEQ=32768, PREFILL_CHUNK=256; no settings changed.':'synthetic provider',
    initialListener,healthBefore,prompt,compilerPrefix:prefix,started:new Date().toISOString()});
  server=createServer(async(req,res)=>{
    let controller;
    try{
      assert.equal(req.method,'POST');assert.equal(req.url,'/v1/chat/completions');assert.equal(inFlight,false);assert.ok(turn===1||turn===2);assert.ok(++turnRequests<=32,'Measurement request cap');inFlight=true;
      const parts=[];let size=0;for await(const bytes of req){size+=bytes.length;assert.ok(size<=1048576);parts.push(bytes);}
      const bytes=Buffer.concat(parts),body=JSON.parse(bytes),c=await count(bytes);assert.equal(body.max_tokens,turn===1?6144:8192);
      const ordinal=++requests;await save(`request-${ordinal}.json`,{turn,body,count:c});
      if(live)noClients();controller=new AbortController();res.on('close',()=>{if(!res.writableEnded)controller.abort();});
      const start=performance.now(),r=await fetch(base+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json',Connection:'close'},body:bytes,redirect:'error',signal:controller.signal});
      assert.equal(r.status,200);const metric={turn,ordinal,count:c,headersSeconds:(performance.now()-start)/1000,wallSeconds:null,responseBytes:0};
      measures.push(metric);res.writeHead(200,{'Content-Type':'text/event-stream'});
      for await(const chunk of r.body){metric.responseBytes+=chunk.length;assert.ok(metric.responseBytes<=64*1024*1024,'Bounded smoke response limit');if(!res.write(chunk))await once(res,'drain');}
      metric.wallSeconds=(performance.now()-start)/1000;res.end();
    }catch(e){proxyFailure=String(e);controller?.abort();if(!res.headersSent)res.writeHead(413);res.end();}
    finally{inFlight=false;}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const proxy=`http://127.0.0.1:${server.address().port}`,db=path.join(out,'session.sqlite');
  const broker=createFileBroker(db+'.workspaces',prefix);
  runtime=await createRuntime(proxy,db,{...broker,execute:(...args)=>{assert.ok(++turnTools<=64,'Measurement tool cap');return broker.execute(...args);}});
  for(turn=1;turn<=2;turn++){
    turnRequests=0;turnTools=0;
    activeId=await runtime.create();const profile=await runtime.profile(activeId);const enabled=await runtime.setAgentMode(activeId,profile.version,true,assets);
    if(turn===1)await runtime.setBudget(activeId,enabled.budget.settings.version,'chat');
    if(fixture)fixture.plan([{name:'write',args:{path:'main.mojo',content:source}},{name:'mojo',args:{path:'main.mojo'}},null]);
    if(live){await pause(100);noClients();}
    const start=performance.now();await runtime.submit(activeId,prompt);
    const deadline=Date.now()+20*60*1000+30000;
    while(runtime.busy()){if(Date.now()>deadline||proxyFailure)throw Error(proxyFailure??'Smoke settlement deadline');if(live)onlyOurClients();await pause(1000);}
    assert.equal(proxyFailure,null);const bridge=await runtime.bridge(activeId,async()=>{}),snapshot=bridge.snapshot;await bridge.stop();
    await save(`turn-${turn}.json`,snapshot);
    const entries=snapshot.pi.entries,budget=await runtime.budget(activeId);
    assert.equal(budget.run.status,'finished');assert.equal(budget.run.stopReason,'complete');
    const readouts=entries.filter(e=>e.kind==='glimmer.readout.turn').map(e=>e.data),toolResults=entries.filter(e=>e.kind==='pi.tool-result');
    assert.ok(readouts.length>0&&readouts.every(t=>t.status==='complete'));
    assert.ok(readouts.every(t=>t.records.some(r=>r.source==='generated'&&r.emotion&&r.workspace)));
    assert.ok(toolResults.length>=2,'Both write and Mojo results must be retained');
    const savedSource=await readFile(db+`.workspaces/workspaces/${activeId}/main.mojo`,'utf8');assert.equal(savedSource,source);
    const calls=entries.flatMap(e=>e.model??[]).flatMap(m=>Array.isArray(m.content)?m.content:[]).filter(b=>b.type==='toolCall');
    const mojoId=calls.findLast(c=>c.name==='mojo')?.id;assert.ok(mojoId);
    const mojoMessages=entries.flatMap(e=>e.model??[]).filter(m=>m.role==='toolResult'&&m.toolCallId===mojoId);
    const text=mojoMessages.flatMap(m=>m.content).filter(c=>c.type==='text').map(c=>c.text).join('');
    const result=JSON.parse(text);assert.equal(result.compile.exit_code,0);assert.equal(result.run.exit_code,0);assert.equal(result.run.stdout.trim(),'glimmer-agent-budget-ok');
    results.push({turn,conversation:activeId,wallSeconds:(performance.now()-start)/1000,budget:budget.run,profileDigest:enabled.profile.active.digest,
      readoutRequests:readouts.length,generatedReadoutRows:readouts.reduce((n,t)=>n+t.records.filter(r=>r.source==='generated').length,0),toolResults:toolResults.length,source:sha(savedSource),compile:0,run:0});
    activeId=null;
  }
}catch(e){failed=String(e);}
finally{
  if(runtime&&activeId){try{await runtime.abort(activeId);const b=await runtime.bridge(activeId,async()=>{});await save('failed-turn.json',b.snapshot);await b.stop();}catch(e){failed??=String(e);}}
  try{await runtime?.close();}catch(e){failed??=String(e);}
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  await fixture?.close();
  if(live){try{await pause(1000);noClients();assert.equal(ss('listen'),initialListener);const r=await fetch(upstream+'/health',{headers:{Connection:'close'},redirect:'error',signal:AbortSignal.timeout(5000)});healthAfter=await r.json();assert.equal(healthAfter.ready,true);assert.equal(healthAfter.readout?.supported,true);}catch(e){failed??=String(e);}}
  await save('result.json',{status:failed?'failed':'pass',failed,proxyFailure,results,measures,healthAfter,finished:new Date().toISOString()});
}
console.log('Agent budget evidence:',out);if(failed){console.error(failed);process.exitCode=1;}else console.log(live?'LIVE AGENT BUDGET VERIFIED':'FIXTURE AGENT BUDGET VERIFIED');
