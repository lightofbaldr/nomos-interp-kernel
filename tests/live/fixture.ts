// Synthetic HTTP producer, never used by application code. The separate recorded
// producer fixture tests protect this fake from becoming its own contract oracle.
import http from 'node:http';
import {mkdtemp,writeFile,mkdir} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
export const hash=(s:string|Uint8Array)=>createHash('sha256').update(s).digest('hex');
export const pause=(ms:number)=>new Promise(r=>setTimeout(r,ms));
export async function until(fn:()=>unknown,ms=5000){const end=Date.now()+ms;while(!await fn()){if(Date.now()>end)throw Error('Timed out');await pause(10);}}
/** A root availability probe is not a model request. Refuse it without parsing
 * a JSON body, changing the response plan or masking malformed completion POSTs. */
export function rejectRootProbe(req:http.IncomingMessage,res:http.ServerResponse){
  if(req.url!=='/'||!['GET','HEAD'].includes(req.method??''))return false;
  res.writeHead(404,{'Content-Type':'text/plain','Cache-Control':'no-store'});res.end();return true;
}
export async function fakeKernel(){
  const root=await mkdtemp(path.join(os.tmpdir(),'glimmer-pi-test-'));
  const names=['calm','joyful','sad','afraid','angry','desperate','compassionate'];
  const assets:{id:string;kind:string;dir:string}[]=[];
  for(const kind of ['directions','lens']){
    const dir=path.join(root,kind);await mkdir(dir);
    const manifest=JSON.stringify({kind,E:names.length,names,layers:Object.fromEntries((kind==='directions'?[20,24,29]:[36,40,44,48]).map(l=>[l,{}]))});
    await writeFile(path.join(dir,'manifest.json'),manifest);assets.push({id:hash(manifest),kind,dir});
  }
  const registry=path.join(root,'registry.json');await writeFile(registry,JSON.stringify({v:1,assets}));
  const tokenizer=JSON.stringify({model:{type:'BPE',vocab:{...Object.fromEntries(Array.from({length:30},(_,i)=>[`Ġword${i}`,503+i])),'Ġcannot':501,'Ġinvent':502}},decoder:{type:'ByteLevel'},added_tokens:[]});
  const tokenizerPath=path.join(root,'tokenizer.json');await writeFile(tokenizerPath,tokenizer);
  const requests:any[]=[],errors:string[]=[];let fault='',hold=false,cacheRequests=0,closing=false,pending=0,drained:(()=>void)|undefined;
  let reply={thinking:'Let me think carefully. ',answer:'Hello from Pi.',finish:'stop'};
  const server=http.createServer(async(req,res)=>{
    pending++;let bodyBytes=0;
    try{
    if(rejectRootProbe(req,res))return;
    if(req.url==='/health'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ready:true,model:'fake',engine:'fake',spec:true,readout:{supported:true,assets:assets.map(a=>a.id)}}));return;}
    if(req.url==='/v1/kvstore/stats'){cacheRequests++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({prepares:7,prompt_tokens:100,reused_tokens:75,restores_ram:4,restores_nvme:1,forks:2,ram_bytes:1024,nvme_bytes:2048,ram_evictions:0,nvme_evictions:0}));return;}
    if(req.method!=='POST'||req.url!=='/v1/chat/completions')throw Error(`Unexpected fixture request ${req.method} ${req.url}`);
    let raw='';for await(const b of req){bodyBytes+=b.length;raw+=b;}
    const body=JSON.parse(raw);requests.push(body);const n=body.nomos,r=n?.readout,response={...reply};
    const id=`chatcmpl-${requests.length}`,header={v:'nomos.readout/1',conversation:n?.conversation??null,turn:n?.turn??null,response_id:id,
      settings:r?{...r,emotion:{...r.emotion,names},workspace:r.workspace??null}:null,steer:n?.steer?{...n.steer,from_pos:2}:null};
    const records=Array.from({length:43},(_,pos)=>({pos,token:pos===42?503:504,source:pos<3?'prompt':'generated',channel:pos<3?null:pos<41?'reasoning':'answer',
      emotion:{proj:r?.emotion.layers.map(()=>names.map((_,i)=>(pos<36?1:12)+i)),norm:r?.emotion.layers.map(()=>100)},
      workspace:pos<3||!r?.workspace?null:{top:r.workspace.layers.map(()=>Array.from({length:r.workspace.k},(_,i)=>[(pos<36||pos===38?503:501)+i,10-i]))},
      missing:pos<3?(r?.workspace?['channel_prompt_v1','workspace_prompt_v1']:['channel_prompt_v1']):[]}));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    const send=(delta:unknown,finish:string|null=null,nomos?:unknown)=>res.write(`data: ${JSON.stringify({id,object:'chat.completion.chunk',created:1,model:'nomos-muse-glimmer-30b',choices:[{index:0,delta,finish_reason:finish}],...(nomos?{nomos}:{})})}\n\n`);
    send({role:'assistant'});send({reasoning_content:response.thinking});
    for(let i=0;i<records.length;i++){
      if(res.destroyed)return;
      send(i===42?{content:response.answer}:{},null,r?{...(i===0?{header}:{}),readout:[records[i]]}:undefined);
      await pause(3);
    }
    if(hold)return;
    const summary={header,ranges:{reused:[0,0],prompt:r?[0,3]:[0,0],generated:r?[3,43]:[0,0]},counts:{records:r?43:0,emotion_valid:r?43:0,workspace_valid:r?.workspace?40:0},missing:{kv_reused:0,natural_stop:1,settle_trimmed:0,...(!r?{readout_off:1}:{})}};
    send({},response.finish,fault==='summary'?undefined:n?{summary}:undefined);res.end('data: [DONE]\n\n');
    }catch(e){
      // Intentional client cancellation or fixture teardown can interrupt body
      // parsing. Join those handlers; retain every other failure for close().
      if(!closing&&!req.aborted)errors.push(`${req.method} ${req.url} bytes=${bodyBytes} complete=${req.complete}: ${String(e)}`);
      if(!res.destroyed){if(!res.headersSent)res.writeHead(400);res.end();}
    }finally{pending--;if(!pending)drained?.();}
  });
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${(server.address() as any).port}`;
  return {root,base,requests,errors,pending:()=>pending,registry,tokenizerPath,tokenizerPin:hash(tokenizer),assets:{supported:true,error:null,directions:[{id:assets[0].id,names,layers:[20,24,29]}],lenses:[{id:assets[1].id,layers:[36,40,44,48]}],tokenizer:hash(tokenizer)},
    cacheRequests:()=>cacheRequests,fault:(v:string)=>{fault=v;},hold:(v:boolean)=>{hold=v;},reply:(v:Partial<typeof reply>)=>{reply={...reply,...v};},async close(){closing=true;server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));await new Promise<void>(r=>{if(pending)drained=r;else r();});if(errors.length)throw Error('Kernel fixture errors: '+errors.join('; '));}};
}
