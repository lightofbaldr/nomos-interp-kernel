import {json,type RequestEvent} from '@sveltejs/kit';
import {sameOrigin} from '$lib/server/origin';
import {recording,recordings} from '$lib/server/recorded-workspace';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  const id=url.searchParams.get('id');
  if(!id)return json({recordings:recordings.map(({id,title})=>({id,title}))},options);
  try{return json(await recording(id),options);}
  catch{return json({error:'Recording unavailable or its identity changed. No data was substituted.'},{status:404,...options});}
}
