import http from 'node:http';
import {fakeKernel} from '../live/fixture.ts';
/** Synthetic loopback provider: real tool execution is tested separately. */
export async function agentKernel(){
  const f=await fakeKernel();let plan:({name:string;args:Record<string,unknown>}|null)[]=[];
  const requests:any[]=[],errors:unknown[]=[];
  const server=http.createServer(async(req,res)=>{
    try{
      if(req.url==='/health'){const r=await fetch(f.base+'/health');res.setHeader('Content-Type','application/json');res.end(await r.text());return;}
      if(req.url!=='/v1/chat/completions')throw Error('Unexpected synthetic endpoint');
      let raw='';for await(const b of req)raw+=b;requests.push(JSON.parse(raw));
      const call=plan.shift();f.reply({answer:call?'':'Synthetic agent task complete.',finish:call?'tool_calls':'stop'});
      const upstream=await fetch(f.base+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json'},body:raw});
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      let buffer='';const decoder=new TextDecoder();
      for await(const bytes of upstream.body!){buffer+=decoder.decode(bytes,{stream:true});let end;
        while((end=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);
          if(frame==='data: [DONE]'){res.write(frame+'\n\n');continue;}
          const data=JSON.parse(frame.slice(6));
          if(call){for(const row of data.nomos?.readout??[])if(row.channel==='answer')row.channel='tool';
            if(data.choices[0].finish_reason==='tool_calls')data.choices[0].delta={tool_calls:[{index:0,id:`agent-call-${requests.length}`,type:'function',function:{name:call.name,arguments:JSON.stringify(call.args)}}]};}
          res.write(`data: ${JSON.stringify(data)}\n\n`);
        }
      }res.end();
    }catch(e){errors.push(e);if(!res.headersSent)res.writeHead(500);res.end();}
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {...f,base:`http://127.0.0.1:${(server.address() as any).port}`,requests,errors,plan:(p:typeof plan)=>{plan=p;},
    close:async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));await f.close();}};
}
