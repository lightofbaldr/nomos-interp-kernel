import {json,type RequestEvent} from '@sveltejs/kit';
import {sameOrigin} from '$lib/server/origin';
import {recording,recordings} from '$lib/server/recorded-workspace';
import {step2Source} from '$lib/server/workspace-step2';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  const id=url.searchParams.get('id'),evidence=url.searchParams.get('evidence');
  try{
    if([...url.searchParams.keys()].some(k=>!['id','evidence'].includes(k))||url.searchParams.size>1)throw Error('Unknown query.');
    if(evidence){if(evidence!=='step2-precheck')throw Error('Unknown evidence.');return json(await step2Source.precheckEvidence(),options);}
    if(!id)return json({recordings:recordings.map(({id,title})=>({id,title})),step2:await step2Source.catalog()},options);
    return json(await recording(id),options);
  }
  catch{return json({error:'Recording unavailable or its identity changed. No data was substituted.'},{status:404,...options});}
}
