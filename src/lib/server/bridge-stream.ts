import type {Bridge,BridgeMessage} from 'glimmer-agent/bridge';

/** Snapshot is installed before any raced event. Browser disconnect stops only this bridge, not its conversation. */
export function bridgeStream(attach:(send:(m:BridgeMessage)=>Promise<void>)=>Promise<Bridge>,signal:AbortSignal){
  let bridge:Bridge|undefined,ended=false,release:(()=>void)|undefined;
  let control:ReadableStreamDefaultController<Uint8Array>;
  const encoder=new TextEncoder();
  const stop=()=>{if(ended)return;ended=true;release?.();void bridge?.stop().catch(()=>{});signal.removeEventListener('abort',abort);};
  const abort=()=>{stop();try{control.close();}catch{/* stream already canceled */}};
  const send=async(data:unknown)=>{
    if(ended)return;
    const bytes=encoder.encode(`data: ${JSON.stringify(data)}\n\n`);
    if(bytes.length>64_000_000)throw Error('Bridge frame too large.');
    control.enqueue(bytes);
    if((control.desiredSize??0)<=0)await new Promise<void>(resolve=>{release=resolve;});
  };
  const stream=new ReadableStream<Uint8Array>({
    start(c){control=c;signal.addEventListener('abort',abort,{once:true});if(signal.aborted){abort();return;}
      let held:BridgeMessage[]|null=[],heldBytes=0;
      // Do not return this promise from start: a large snapshot needs pull() to release backpressure.
      void (async()=>{
      try{
        bridge=await attach(async m=>{if(ended)return;if(held!==null){
          heldBytes+=encoder.encode(JSON.stringify(m)).length;
          if(heldBytes>8_000_000||held.length>=1024){abort();throw Error('Bridge attachment overflow. Reconnect for a fresh snapshot.');}
          held.push(m);
        }else await send(m);});
        if(ended){await bridge.stop();return;}
        await send({type:'bridge.snapshot',snapshot:bridge.snapshot});
        // Keep holding while draining: no newly delivered event may overtake a held one.
        while(held.length&&!ended)await send(held.shift());
        held=null;
      }catch(e){stop();try{c.error(e);}catch{/* canceled */}}
      })();
    },
    pull(){const done=release;release=undefined;done?.();},
    cancel(){stop();}
  },{highWaterMark:1_000_000,size:chunk=>chunk.byteLength});
  return new Response(stream,{headers:{'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'}});
}
