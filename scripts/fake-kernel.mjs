import {createServer} from 'node:http';
import {once} from 'node:events';
export const thought='  Compare the known capitals.\n🙂  ',answer='Paris.\n';
export const frame=(delta={},finish=null)=>`data: ${JSON.stringify({id:'fixture-only',choices:[{index:0,delta,finish_reason:finish}]})}\r\n\r\n`;
export async function fakeKernel(){
  const state={requests:[],statsRequests:0,ready:true,failStats:false,active:0,aborted:0};
  const stats={prepares:1,prompt_tokens:120,reused_tokens:100,restores_ram:0,restores_nvme:0,forks:0,ram_bytes:1048576,nvme_bytes:2097152,ram_evictions:0,nvme_evictions:0};
  const timers=new Set();
  const server=createServer(async(req,res)=>{
    const json=(code,data)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
    if(req.url==='/health')return json(200,{ready:state.ready,model:'nomos-muse-fixture',engine:'ok',spec:true,kvstore:{status:'open',dir:'/not-for-the-browser'}});
    if(req.url==='/v1/kvstore/stats'){state.statsRequests++;return json(state.failStats?503:200,state.failStats?{status:'busy'}:stats);}
    if(req.url!=='/v1/chat/completions')return json(404,{});
    let body='';for await(const chunk of req)body+=chunk;
    const request=JSON.parse(body);state.requests.push(request);const prompt=request.messages.at(-1).content;
    if(prompt.includes('[fail]'))return json(500,{detail:'fixture refusal'});
    state.active++;stats.prepares++;stats.prompt_tokens+=20;stats.reused_tokens+=10;
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    let complete=false,timer;
    res.on('close',()=>{state.active--;if(!complete)state.aborted++;clearInterval(timer);timers.delete(timer);});
    if(prompt.includes('[slow]')){
      res.write(frame({reasoning_content:'  partial reasoning 🙂\n'}));
      timer=setInterval(()=>res.write(': keepalive\n\n'),200);timers.add(timer);return;
    }
    if(prompt.includes('[interrupt]')){res.write(frame({reasoning_content:'Kept despite interruption.'}));complete=true;res.end();return;}
    if(prompt.includes('[noanswer]')){res.end(frame({reasoning_content:thought})+frame({},'length')+'data: [DONE]\n\n');complete=true;return;}
    const wire=Buffer.from(frame({role:'assistant'})+frame({reasoning_content:thought})+frame({content:prompt.includes('[markup]')?'<img src="x" onerror="window.__modelInjected=1">':answer})+frame({},'stop')+'data: [DONE]\r\n\r\n');
    let offset=0;timer=setInterval(()=>{if(offset>=wire.length){complete=true;res.end();clearInterval(timer);timers.delete(timer);return;}res.write(wire.subarray(offset,offset+7));offset+=7;},2);timers.add(timer);
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  return {state,stats,url:`http://127.0.0.1:${server.address().port}`,close:async()=>{for(const timer of timers)clearInterval(timer);server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}};
}
