import {json,type RequestEvent} from '@sveltejs/kit';
import {withService} from '$lib/server/pi-service';
import {sameOrigin,jsonBody} from '$lib/server/origin';
import {agentMode} from '$lib/agent/budget';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    if([...url.searchParams.keys()].some(k=>k!=='conversation')||url.searchParams.getAll('conversation').length!==1)throw Error();
    return await withService(async runtime=>json(await runtime.budget(url.searchParams.get('conversation')!),options));
  }catch{return json({error:'Conversation budget unavailable.'},{status:404,...options});}
}
export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const b=await jsonBody(request,4096);
    if(Object.keys(b).some(k=>!['conversation','version','mode'].includes(k))||typeof b.conversation!=='string')throw Error('Invalid budget setting.');
    const mode=agentMode(b.mode);
    return await withService(async runtime=>json({budget:await runtime.setBudget(b.conversation as string,b.version as number,mode)},options));
  }catch(e){return json({error:e instanceof Error?e.message:'Budget unavailable.'},{status:400,...options});}
}
