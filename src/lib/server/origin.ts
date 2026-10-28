/** Loopback, same-origin control plane. Not authentication for a shared/public deployment. */
export function sameOrigin(request:Request,url:URL){
  if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname))return false;
  const origin=request.headers.get('origin');
  if(origin!==null)return origin===url.origin;
  if(request.method!=='GET'||request.headers.get('sec-fetch-site')!=='same-origin')return false;
  try{return new URL(request.headers.get('referer')??'').origin===url.origin;}catch{return false;}
}
export async function jsonBody(request:Request){
  if(!request.headers.get('content-type')?.startsWith('application/json')||!request.body)throw Error('JSON body required.');
  const reader=request.body.getReader();let size=0,text='';const decode=new TextDecoder();
  try{for(;;){const p=await reader.read();if(p.done)break;size+=p.value.length;if(size>32000)throw Error('Request too large.');text+=decode.decode(p.value,{stream:true});}
    const value=JSON.parse(text+decode.decode());
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('JSON object required.');
    return value as Record<string,unknown>;
  }finally{await reader.cancel().catch(()=>{});}
}
