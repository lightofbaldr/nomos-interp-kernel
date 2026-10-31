import {json,type RequestEvent} from '@sveltejs/kit';
import {env} from '$env/dynamic/private';
import {service} from '$lib/server/pi-service';
import {sameOrigin,jsonBody} from '$lib/server/origin';
import {createImporter,importRoots} from '$lib/server/profile-import';
import {profileAction} from '$lib/server/profile-api';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
let imports:ReturnType<typeof createImporter>|undefined;
const importer=()=>imports??=createImporter(importRoots(env.GLIMMER_HARNESS_IMPORT_ROOTS));
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const id=url.searchParams.get('conversation');if(!id)throw Error('Conversation required.');
    return json({profile:await (await service()).profile(id),roots:importer().roots()},options);
  }catch(e){return json({error:e instanceof Error?e.message:'Profile unavailable.'},{status:400,...options});}
}
export async function POST({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{return json(await profileAction(await service(),importer(),await jsonBody(request,524288)),options);}
  catch(e){return json({error:e instanceof Error?e.message:'Profile operation failed.'},{status:400,...options});}
}
