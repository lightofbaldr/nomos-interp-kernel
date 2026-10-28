export type ChatMessage={role:'system'|'user'|'assistant';content:string;reasoning_content?:string};
export type Health={ready:boolean;model:string;engine:string;spec:boolean;store:string};
export const statKeys=['prepares','prompt_tokens','reused_tokens','restores_ram','restores_nvme','forks','ram_bytes','nvme_bytes','ram_evictions','nvme_evictions'] as const;
export type KVStats=Record<typeof statKeys[number],number>;
export type LiveState={health:Health|null;stats:KVStats|null;statsAt:string|null;error:string;statsError:string;busy:boolean;firstTextMs:number|null;elapsedMs:number|null;finish:string|null;pi?:import('./pi/model').PiView;assets?:import('./pi/model').Assets;conversation?:string;labels?:Record<string,string|null>;
  selection?:{request:string;pos:number};emotionLayer?:number;
  select?:(selection?:{request:string;pos:number})=>void;selectLayer?:(layer:number)=>void;
  command?:(action:'readout'|'steer',value:unknown)=>Promise<void>;
  alertRules?:import('./pi/alerts').AlertRules;alerts?:import('./pi/alerts').AlertResult;
  setAlertRules?:(rules:import('./pi/alerts').AlertRules)=>void};
export const emptyLiveState=():LiveState=>({health:null,stats:null,statsAt:null,error:'',statsError:'',busy:false,firstTextMs:null,elapsedMs:null,finish:null});
// Fixed for the entire conversation. In particular, never inject a changing
// date into the system prompt: that destroys the KV prefix across midnight.
export const SYSTEM_MESSAGE='You are a helpful AI assistant.\nKnowledge cutoff: 2026-01-04.\n\nReasoning strength: high.';

export function readStats(value:unknown):KVStats{
  if(!value||typeof value!=='object')throw Error('No KV statistics supplied.');
  const v=value as Record<string,unknown>;
  if(!statKeys.every(k=>Number.isSafeInteger(v[k])&&Number(v[k])>=0))throw Error('Invalid KV statistics received.');
  return Object.fromEntries(statKeys.map(k=>[k,v[k]])) as KVStats;
}

/** SSE frames are not tokens. Preserve each field byte-for-byte after UTF-8
 * decoding, including whitespace and replacement characters from the server. */
export async function consumeChat(response:Response,onDelta:(field:'content'|'reasoning_content',text:string)=>void):Promise<string>{
  if(!response.ok){let message='';try{message=(await response.json()).error??'';}catch{/* not a JSON error */}throw Error(message||`Chat failed (${response.status}).`);}
  if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw Error('The server did not return a chat stream.');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',finish:string|null=null,total=0,done=false;
  try{
    while(!done){
      const part=await reader.read();
      if(part.done){buffer+=decoder.decode();break;}
      total+=part.value.byteLength;if(total>4_000_000)throw Error('Chat exceeded the client response limit.');
      buffer+=decoder.decode(part.value,{stream:true});
      let boundary:RegExpExecArray|null;
      while((boundary=/\r?\n\r?\n/.exec(buffer))){
        const event=buffer.slice(0,boundary.index);buffer=buffer.slice(boundary.index+boundary[0].length);
        const data=event.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).replace(/^ /,'')).join('\n');
        if(!data)continue;
        if(data==='[DONE]'){done=true;break;}
        let frame;try{frame=JSON.parse(data);}catch{throw Error('Malformed chat stream received.');}
        if(frame.error)throw Error('The kernel reported a streaming error.');
        const choice=frame.choices?.[0];if(!choice)throw Error('Chat stream is missing its completion choice.');
        const delta=choice.delta??{};
        if(delta.tool_calls||choice.finish_reason==='tool_calls')throw Error('Tool calls are not enabled in this interface.');
        for(const field of ['reasoning_content','content'] as const){
          if(delta[field]!==undefined&&delta[field]!==null){if(typeof delta[field]!=='string')throw Error('Invalid streamed text field.');onDelta(field,delta[field]);}
        }
        if(choice.finish_reason!==null&&choice.finish_reason!==undefined){if(typeof choice.finish_reason!=='string')throw Error('Invalid completion status.');finish=choice.finish_reason;}
      }
      if(buffer.length>256_000)throw Error('A chat stream frame exceeded the client limit.');
    }
    if(!done||!finish)throw Error('Connection ended before completion. Partial text was kept; this reply is not complete.');
    return finish;
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
