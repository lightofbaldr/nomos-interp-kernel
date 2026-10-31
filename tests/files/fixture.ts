import http from 'node:http';
import {fakeKernel} from '../live/fixture.ts';
export async function fileKernel(){
  const f=await fakeKernel();let plan:({name:string;args:Record<string,unknown>}|null)[]=[];
  const requests:any[]=[];
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/health'){const r=await fetch(f.base+'/health');res.setHeader('Content-Type','application/json');res.end(await r.text());return;}
    let raw='';for await(const b of req)raw+=b;requests.push(JSON.parse(raw));
    const call=plan.shift(),id=`chatcmpl-files-${requests.length}`;
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    const chunk=(delta:unknown,finish:string|null=null)=>res.write(`data: ${JSON.stringify({id,object:'chat.completion.chunk',created:1,model:'nomos-muse-glimmer-30b',choices:[{index:0,delta,finish_reason:finish}]})}\n\n`);
    if(call){chunk({tool_calls:[{index:0,id:`file-call-${requests.length}`,type:'function',function:{name:call.name,arguments:JSON.stringify(call.args)}}]});chunk({},'tool_calls');}
    else{chunk({content:'File task complete.'});chunk({},'stop');}
    res.end('data: [DONE]\n\n');
  });
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
  return {...f,base:`http://127.0.0.1:${(server.address() as any).port}`,requests,plan:(p:typeof plan)=>{plan=p;},
    close:async()=>{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));await f.close();}};
}
