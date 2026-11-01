import {json,type RequestEvent} from '@sveltejs/kit';
import {service,capabilities,tokenLabels,metaAvailability} from '$lib/server/pi-service';
import {sameOrigin,jsonBody} from '$lib/server/origin';
import {bridgeStream} from '$lib/server/bridge-stream';
import type {ReadoutConfig,Steer} from 'glimmer-agent/extension';
import {env} from '$env/dynamic/private';

const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  if(!env.NOMOS_API_BASE_URL)return json({conversations:[],health:null,assets:{supported:false,error:'No kernel endpoint is configured.',directions:[],lenses:[],tokenizer:null},error:'No kernel endpoint is configured.'},options);
  try{
    const runtime=await service(),id=url.searchParams.get('conversation');
    if(id)return bridgeStream(send=>runtime.bridge(id,send),request.signal);
    if(url.searchParams.has('history'))return json({conversations:await runtime.list()},options);
    return json({conversations:await runtime.list(),...await capabilities(),annotator:await metaAvailability()},options);
  }catch(e){return json({error:e instanceof Error?e.message:'Agent unavailable.'},{status:503,...options});}
}
export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const body=await jsonBody(request);
    const fields:Record<string,string[]>={labels:['ids'],create:[],submit:['text'],retry:['target'],generation:['version','sampling'],abort:[],readout:['readout'],steer:['steer']};
    if(typeof body.action!=='string'||!Object.hasOwn(fields,body.action))throw Error('Unknown control action.');
    if(Object.keys(body).some(k=>!['action','conversation',...fields[body.action as string]].includes(k)))throw Error('Unknown control field.');
    if(body.action==='labels')return json(await tokenLabels(body.ids),options);
    const runtime=await service();
    if(body.action==='create')return json({conversation:await runtime.create()},options);
    if(typeof body.conversation!=='string')throw Error('Conversation required.');
    const id=body.conversation;
    if(body.action==='submit')return json(await runtime.submit(id,body.text as string),options);
    if(body.action==='retry')return json(await runtime.retry(id,body.target as number),options);
    if(body.action==='generation')return json(await runtime.setGeneration(id,body.version as number,body.sampling as boolean),options);
    if(body.action==='abort'){await runtime.abort(id);return json({aborted:true},options);}
    if(body.action==='readout')return json(await runtime.readout(id,body.readout as ReadoutConfig,(await capabilities()).assets),options);
    if(body.action==='steer')return json(await runtime.steer(id,body.steer as Steer|null,(await capabilities()).assets),options);
    throw Error('Unknown control action.');
  }catch(e){return json({error:e instanceof Error?e.message:'Agent operation failed.'},{status:400,...options});}
}
