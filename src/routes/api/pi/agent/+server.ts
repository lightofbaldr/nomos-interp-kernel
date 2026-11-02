import {json,type RequestEvent} from '@sveltejs/kit';
import {withService,capabilities} from '$lib/server/pi-service';
import {sameOrigin,jsonBody} from '$lib/server/origin';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const b=await jsonBody(request,4096);
    if(Object.keys(b).some(k=>!['conversation','version','enabled'].includes(k))||typeof b.conversation!=='string'||typeof b.enabled!=='boolean')throw Error('Invalid Agent mode setting.');
    return await withService(async runtime=>json(await runtime.setAgentMode(b.conversation as string,b.version as number,b.enabled as boolean,b.enabled?(await capabilities()).assets:undefined),options));
  }catch(e){return json({error:e instanceof Error?e.message:'Agent mode unavailable.'},{status:400,...options});}
}
