import {json,type RequestEvent} from '@sveltejs/kit';
import {sameOrigin,jsonBody} from '$lib/server/origin';
import {previewConversationPurge,purgeConversations} from '$lib/server/pi-service';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};

export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const b=await jsonBody(request,4096);
    if(b.action==='preview'&&Object.keys(b).every(k=>['action','target'].includes(k)))return json(await previewConversationPurge(b.target),options);
    if(b.action==='purge'&&Object.keys(b).every(k=>['action','target','revision','confirmation'].includes(k)))return json(await purgeConversations(b.target,b.revision,b.confirmation),options);
    throw Error('Invalid conversation data request.');
  }catch(e){return json({error:e instanceof Error?e.message:'Conversation deletion failed. No success was recorded.'},{status:409,...options});}
}
