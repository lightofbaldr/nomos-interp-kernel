/** Loopback, same-origin control plane. Not authentication for a shared/public deployment. */
const loopbackHosts=['127.0.0.1','localhost','[::1]'];

/** adapter-node's fixed ORIGIN can differ from a browser's loopback alias.
 * Redirect page navigations before they issue API requests; never relax API
 * origin checks or trust a forwarded-host header. The destination is fixed by
 * the operator, and aliases must use the same port.
 */
export function canonicalLoopbackPage(request:Request,url:URL,configuredOrigin:string|undefined):string|null{
  if(!configuredOrigin||!['GET','HEAD'].includes(request.method)
    ||!request.headers.get('accept')?.includes('text/html')
    ||url.pathname==='/api'||url.pathname.startsWith('/api/'))return null;
  try{
    const target=new URL(configuredOrigin),host=request.headers.get('host');
    if(!['http:','https:'].includes(target.protocol)||!loopbackHosts.includes(target.hostname)
      ||target.username||target.password||target.pathname!=='/'||target.search||target.hash||!host)return null;
    const incoming=new URL(`${target.protocol}//${host}`);
    if(!loopbackHosts.includes(incoming.hostname)||incoming.username||incoming.password
      ||incoming.pathname!=='/'||incoming.search||incoming.hash||incoming.port!==target.port
      ||incoming.origin===target.origin)return null;
    target.pathname=url.pathname;
    target.search=url.search;
    return target.href;
  }catch{return null;}
}

export function sameOrigin(request:Request,url:URL){
  if(!loopbackHosts.includes(url.hostname))return false;
  const origin=request.headers.get('origin');
  if(origin!==null)return origin===url.origin;
  if(request.method!=='GET'||request.headers.get('sec-fetch-site')!=='same-origin')return false;
  try{return new URL(request.headers.get('referer')??'').origin===url.origin;}catch{return false;}
}
export async function jsonBody(request:Request,maxBytes=32000){
  if(!request.headers.get('content-type')?.startsWith('application/json')||!request.body)throw Error('JSON body required.');
  const reader=request.body.getReader();let size=0,text='';const decode=new TextDecoder('utf-8',{fatal:true});
  try{for(;;){const p=await reader.read();if(p.done)break;size+=p.value.length;if(size>maxBytes)throw Error('Request too large.');text+=decode.decode(p.value,{stream:true});}
    const value=JSON.parse(text+decode.decode());
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('JSON object required.');
    return value as Record<string,unknown>;
  }finally{await reader.cancel().catch(()=>{});}
}
