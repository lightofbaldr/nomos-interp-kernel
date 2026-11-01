import {json,type RequestEvent} from '@sveltejs/kit';
import {env} from '$env/dynamic/private';
import os from 'node:os';
import path from 'node:path';
import {sameOrigin} from '$lib/server/origin';
import {piBusy} from '$lib/server/pi-service';
import {systemProbe,hostSnapshot} from '$lib/server/system-probe';
let probe:ReturnType<typeof systemProbe>|undefined,busy=false;
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  if([...url.searchParams.keys()].some(k=>k!=='cache')||url.searchParams.has('cache')&&url.searchParams.get('cache')!=='1')return json({error:'Invalid system request.'},{status:400,...options});
  try{busy=await piBusy();}catch{busy=true;}
  probe??=systemProbe({endpoint:env.NOMOS_API_BASE_URL,busy:()=>busy,host:()=>hostSnapshot(path.dirname(env.GLIMMER_SESSION_DB||path.join(os.homedir(),'nomos_data/glimmer-lab/sessions.sqlite')))});
  return json(await probe.get(url.searchParams.get('cache')==='1'),options);
}
