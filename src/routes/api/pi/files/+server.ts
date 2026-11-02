import {json,type RequestEvent} from '@sveltejs/kit';
import {withService,conversationFiles} from '$lib/server/pi-service';
import {sameOrigin} from '$lib/server/origin';
const options={headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}};
export async function GET({request,url}:RequestEvent){
  if(!sameOrigin(request,url))return json({error:'Same-origin loopback access required.'},{status:403,...options});
  try{
    const p=url.searchParams,keys=[...p.keys()];
    if(new Set(keys).size!==keys.length||keys.some(k=>!['conversation','snapshot','entry'].includes(k)))throw Error('Invalid file selection.');
    const id=p.get('conversation'),snapshot=p.get('snapshot'),entry=p.get('entry');
    if(!id||p.has('entry')!==p.has('snapshot'))throw Error('Choose a listed workspace entry.');
    return await withService(async runtime=>{
      // Read-only validation and a manager lease: purge cannot remove/recreate
      // this conversation while its confined file descriptor is being read.
      await runtime.assertConversation(id);
      const files=conversationFiles();
      return json(entry!==null?await files.read(id,snapshot!,entry):await files.list(id),options);
    });
  }catch{return json({error:'Workspace unavailable or selection changed. Refresh the file list; no other file was substituted.'},{status:404,...options});}
}
