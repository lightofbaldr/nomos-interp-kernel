import type {Wire} from './model.ts';

/** Same-origin bridge SSE. EOF is a disconnect, never a successful turn. Reattach from a fresh snapshot. */
export async function readBridge(response:Response,receive:(message:Wire)=>void){
  if(!response.ok)throw Error((await response.json()).error??`Bridge failed (${response.status}).`);
  if(!response.body || !response.headers.get('content-type')?.includes('text/event-stream'))throw Error('Expected a bridge stream.');
  const reader=response.body.getReader(), decoder=new TextDecoder();let buffer='';
  try{for(;;){
    const part=await reader.read();if(part.done)throw Error('Bridge disconnected. Reconnect to restore the current snapshot.');
    buffer+=decoder.decode(part.value,{stream:true});
    if(buffer.length>64_000_000)throw Error('Bridge frame exceeds the client limit.');
    let match:RegExpExecArray|null;
    while((match=/\r?\n\r?\n/.exec(buffer))){
      const frame=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
      const data=frame.split(/\r?\n/).filter(s=>s.startsWith('data:')).map(s=>s.slice(5).trimStart()).join('\n');
      if(data)receive(JSON.parse(data) as Wire);
    }
  }}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
