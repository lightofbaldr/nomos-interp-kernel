import http from 'node:http';
import {fakeKernel} from '../live/fixture.ts';
export async function fileKernel(){
  const f=await fakeKernel();let plan:({name:string;args:Record<string,unknown>}|null)[]=[];
  const requests:any[]=[],errors:string[]=[];let closing=false,pending=0,drained:(()=>void)|undefined;
  const server=http.createServer(async(req,res)=>{
    pending++;
    try{
    if(req.url==='/health'){const r=await fetch(f.base+'/health');res.setHeader('Content-Type','application/json');res.end(await r.text());return;}
    if(req.method!=='POST'||req.url!=='/v1/chat/completions')throw Error(`Unexpected fixture request ${req.method} ${req.url}`);
    let raw='';for await(const b of req)raw+=b;requests.push(JSON.parse(raw));
    const call=plan.shift(),id=`chatcmpl-files-${requests.length}`;
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    const chunk=(delta:unknown,finish:string|null=null)=>res.write(`data: ${JSON.stringify({id,object:'chat.completion.chunk',created:1,model:'nomos-muse-glimmer-30b',choices:[{index:0,delta,finish_reason:finish}]})}\n\n`);
    if(call){chunk({tool_calls:[{index:0,id:`file-call-${requests.length}`,type:'function',function:{name:call.name,arguments:JSON.stringify(call.args)}}]});chunk({},'tool_calls');}
    else{chunk({content:'File task complete.'});chunk({},'stop');}
    res.end('data: [DONE]\n\n');
    }catch(e){
      // Teardown may cut a request off before its body arrives. Do not turn
      // deliberate closeAllConnections into an unhandled async rejection;
      // completed malformed requests still fail the fixture's close gate.
      if(!closing&&!req.aborted)errors.push(`${req.method} ${req.url}: ${String(e)}`);
      if(!res.destroyed){if(!res.headersSent)res.writeHead(400);res.end();}
    }finally{pending--;if(!pending)drained?.();}
  });
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
  return {...f,base:`http://127.0.0.1:${(server.address() as any).port}`,requests,errors,pending:()=>pending,plan:(p:typeof plan)=>{plan=p;},
    close:async()=>{closing=true;server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));await new Promise<void>(r=>{if(pending)drained=r;else r();});await f.close();if(errors.length)throw Error('File fixture errors: '+errors.join('; '));}};
}
