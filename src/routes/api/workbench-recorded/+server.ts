import {json,type RequestEvent} from '@sveltejs/kit';
import {sameOrigin} from '$lib/server/origin';
import {loadRecordedHarness} from '$lib/server/recorded-harness';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    if(url.searchParams.size)throw Error('No recording selector or path is accepted.');
    return json(await loadRecordedHarness(),options);
  }catch{return json({error:'Recorded comparison unavailable or its identity changed. No data was substituted.'},{status:404,...options});}
}
