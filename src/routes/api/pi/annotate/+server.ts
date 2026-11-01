import {json,type RequestEvent} from '@sveltejs/kit';
import {sameOrigin,jsonBody} from '$lib/server/origin';
import {metaAnnotator,withService} from '$lib/server/pi-service';
import type {AlertRules} from '$lib/pi/alerts';
import {MetaError} from '$lib/server/meta-client';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const b=await jsonBody(request);
    if(Object.keys(b).some(k=>!['conversation','request','rules','consent'].includes(k))||b.consent!==true||typeof b.conversation!=='string'||typeof b.request!=='string')throw Error('Invalid annotation request.');
    return await withService(async runtime=>json({annotation:await metaAnnotator(runtime).annotate(b.conversation as string,b.request as string,b.rules as AlertRules)},options));
  }catch(e){
    // Never relay filesystem, fetch, authentication or provider diagnostics.
    const error=e instanceof MetaError?new MetaError(e.code).message:e instanceof Error&&e.message.includes('60-second cooldown')?'Muse Wonder has a 60-second cooldown per conversation.':'This completed turn could not be annotated. No automatic retry was made.';
    return json({error},{status:400,...options});
  }
}
