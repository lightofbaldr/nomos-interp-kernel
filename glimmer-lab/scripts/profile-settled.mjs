// Browser-test synchronization only. A visible Send button is not a durable
// settlement acknowledgement; profile saves use Pi's live/inbox state.
import assert from 'node:assert/strict';
import {setTimeout as pause} from 'node:timers/promises';

export function profileSettled(message,id){
  assert.equal(message?.type,'bridge.snapshot','Expected a server snapshot');
  assert.equal(String(message.snapshot?.settings?.conversationId),String(id),'Wrong conversation snapshot');
  const pi=message.snapshot?.pi;
  assert.equal(pi?.type,'snapshot','Expected Pi state');
  for(const name of ['inbox','tools','nestedTools','compactions'])assert.ok(Array.isArray(pi[name]),`Missing ${name} state`);
  return !pi.run&&!pi.generation&&['inbox','tools','nestedTools','compactions'].every(name=>pi[name].length===0);
}

export async function waitForProfileSettled(base,id,{timeoutMs=12000,intervalMs=50,fetcher=fetch}={}){
  const deadline=Date.now()+timeoutMs;
  for(;;){
    const remaining=deadline-Date.now();
    if(remaining<=0)throw Error(`Conversation ${id} did not settle before the profile operation`);
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),remaining);
    let reader,message;
    try{
      const response=await fetcher(`${base}/api/pi?conversation=${encodeURIComponent(id)}`,{
        headers:{Origin:base},redirect:'error',signal:controller.signal
      });
      reader=response.body?.getReader();assert.ok(response.ok&&reader,'Server snapshot unavailable');
      const decoder=new TextDecoder();let text='';
      for(;;){
        const part=await reader.read();assert.equal(part.done,false,'Server snapshot ended early');
        text+=decoder.decode(part.value,{stream:true});assert.ok(text.length<=8_000_000,'Test snapshot exceeded its bound');
        const end=text.indexOf('\n\n');
        if(end>=0){assert.ok(text.startsWith('data: '));message=JSON.parse(text.slice(6,end));break;}
      }
    }finally{clearTimeout(timer);controller.abort();await reader?.cancel().catch(()=>{});}
    if(profileSettled(message,id))return;
    await pause(Math.min(intervalMs,Math.max(0,deadline-Date.now())));
  }
}
