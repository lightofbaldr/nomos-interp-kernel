import {env} from '$env/dynamic/private';
import {json} from '@sveltejs/kit';
import type {RequestEvent} from '@sveltejs/kit';
import {readStats,type ChatMessage} from '$lib/live';
import {sameOrigin} from './origin';
import {generationParameters,CHAT_TIMEOUT_MS} from '../pi/generation.ts';

export function configured(){return Boolean(env.NOMOS_API_BASE_URL);}
function endpoint(path:string){
  if(!env.NOMOS_API_BASE_URL)throw Error('No kernel endpoint is configured.');
  const base=new URL(env.NOMOS_API_BASE_URL);
  if(!['http:','https:'].includes(base.protocol)||base.username||base.password||base.search||base.hash||!['','/'].includes(base.pathname))throw Error('Invalid server-side kernel configuration.');
  return new URL(path,base);
}
const options={headers:{'Cache-Control':'no-store'}};
export async function health({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  if(!configured())return json({configured:false,health:null,error:'No kernel endpoint is configured.'},options);
  try{
    const response=await fetch(endpoint('/health'),{signal:AbortSignal.timeout(5000),redirect:'error'});
    if(!response.ok)throw Error(`Kernel is not ready (${response.status}).`);
    const v=await response.json();
    if(typeof v.ready!=='boolean'||typeof v.model!=='string')throw Error('Invalid kernel health response.');
    return json({configured:true,health:{ready:v.ready,model:v.model,engine:typeof v.engine==='string'?v.engine:'unknown',spec:v.spec===true,store:typeof v.kvstore?.status==='string'?v.kvstore.status:'unknown'},error:''},options);
  }catch{return json({configured:true,health:null,error:'Cannot reach the configured kernel. Check the workstation and network.'},options);}
}
export async function stats({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const response=await fetch(endpoint('/v1/kvstore/stats'),{signal:AbortSignal.timeout(5000),redirect:'error'});
    if(!response.ok)throw Error('KV statistics unavailable; the engine may be busy.');
    return json({stats:readStats(await response.json()),at:new Date().toISOString(),error:''},options);
  }catch{return json({stats:null,at:null,error:'KV statistics unavailable. Chat remains independent.'},options);}
}
async function boundedBody(request:Request){
  if(!request.body)throw Error('Missing request body.');
  const reader=request.body.getReader();let text='',size=0;const decoder=new TextDecoder();
  try{while(true){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>262144)throw Error('Conversation exceeds the request limit. Start a new conversation.');text+=decoder.decode(part.value,{stream:true});}text+=decoder.decode();return JSON.parse(text);}finally{await reader.cancel().catch(()=>{});}
}
function messagesFrom(value:unknown){
  const v=value as {messages?:unknown;sampling?:unknown};
  if(!v||Object.keys(v).some(k=>!['messages','sampling'].includes(k))||!Array.isArray(v.messages)||v.messages.length<2||v.messages.length>64)throw Error('Send a conversation of 2–64 messages.');
  const messages=v.messages as ChatMessage[];
  let chars=0;
  for(const [i,m] of messages.entries()){
    if(!m||typeof m!=='object'||Object.keys(m).some(k=>!['role','content','reasoning_content'].includes(k))||!['system','user','assistant'].includes(m.role)||typeof m.content!=='string')throw Error('Invalid conversation message.');
    if(m.reasoning_content!==undefined&&(m.role!=='assistant'||typeof m.reasoning_content!=='string'))throw Error('Invalid reasoning history.');
    if((i===0)!==(m.role==='system'))throw Error('Keep one fixed system message at the start.');
    chars+=m.content.length+(m.reasoning_content?.length??0);
  }
  if(chars>64000)throw Error('Conversation exceeds the interface context limit. Start a new conversation.');
  if(messages.at(-1)?.role!=='user'||!messages.at(-1)?.content.trim())throw Error('A new user message is required.');
  return {messages,parameters:generationParameters(v.sampling===undefined?false:v.sampling as boolean)};
}
export async function chat({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Cross-origin chat requests are refused.'},{status:403,...options});
  if(!request.headers.get('content-type')?.startsWith('application/json'))return json({error:'JSON required.'},{status:415,...options});
  let body;try{body=messagesFrom(await boundedBody(request));}catch(e){return json({error:e instanceof Error?e.message:'Invalid request.'},{status:400,...options});}
  const controller=new AbortController();const abort=()=>controller.abort();request.signal.addEventListener('abort',abort,{once:true});
  const timeout=setTimeout(abort,CHAT_TIMEOUT_MS);
  const cleanup=()=>{clearTimeout(timeout);request.signal.removeEventListener('abort',abort);};
  try{
    const upstream=await fetch(endpoint('/v1/chat/completions'),{method:'POST',redirect:'error',signal:controller.signal,headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify({...body.parameters,messages:body.messages,stream:true,enable_thinking:true})});
    if(!upstream.ok||!upstream.body||!upstream.headers.get('content-type')?.includes('text/event-stream')){
      await upstream.body?.cancel();cleanup();return json({error:upstream.status===413?'The conversation is too long for the kernel. Start a new conversation.':`Kernel request failed (${upstream.status}). No completed reply was received.`},{status:upstream.status===413?413:502,...options});
    }
    const reader=upstream.body.getReader();
    return new Response(new ReadableStream({
      async pull(out){try{const part=await reader.read();if(part.done){cleanup();out.close();}else out.enqueue(part.value);}catch(e){cleanup();out.error(e);}},
      async cancel(){controller.abort();cleanup();await reader.cancel().catch(()=>{});}
    }),{headers:{'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no',
      'X-Glimmer-Decoding':body.parameters.temperature===0?'greedy':'sampled','X-Glimmer-Reply-Cap':String(body.parameters.max_tokens),
      'X-Glimmer-Generation':JSON.stringify(body.parameters)}});
  }catch{cleanup();return json({error:'The kernel request could not complete. Check the connection and try again.'},{status:502,...options});}
}
