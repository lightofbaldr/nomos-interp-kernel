import {readFile,statfs} from 'node:fs/promises';
import os from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readStats,type KVStats} from '../live.ts';
import type {HostSnapshot,KernelHealth,SystemSnapshot} from '../inspection/system.ts';
const run=promisify(execFile);
const finite=(s:string|undefined)=>s!==undefined&&s.trim()!==''&&Number.isFinite(Number(s))&&Number(s)>=0?Number(s):null;
export function parseMemory(text:string):HostSnapshot['memory']{
  const values=Object.fromEntries([...text.matchAll(/^([A-Za-z_()]+):\s+(\d+) kB$/gm)].map(m=>[m[1],Number(m[2])*1024]));
  const [total,available,swapTotal,swapFree]=['MemTotal','MemAvailable','SwapTotal','SwapFree'].map(k=>values[k]);
  return [total,available,swapTotal,swapFree].every(Number.isSafeInteger)&&available<=total&&swapFree<=swapTotal?{total,available,swapTotal,swapFree}:null;
}
export function parsePressure(text:string):HostSnapshot['pressure']{
  const value=(kind:string)=>finite(text.match(new RegExp(`^${kind} avg10=([\\d.]+)`, 'm'))?.[1]);
  const some=value('some'),full=value('full');return some===null&&full===null?null:{some,full};
}
export function parseGPUs(text:string):HostSnapshot['gpus']{
  const rows=text.trim().split('\n').filter(Boolean).map(line=>line.split(',').map(x=>x.trim()));
  if(!rows.length||rows.some(r=>r.length!==5||finite(r[1])===null||finite(r[2])===null||Number(r[2])>Number(r[1])))return null;
  return rows.map(([name,total,used,util,temp])=>({name,totalMiB:Number(total),usedMiB:Number(used),utilization:finite(util),temperature:finite(temp)}));
}
/** Fixed read-only local probes. No shell, no browser-controlled argv, no remote SSH. */
export async function hostSnapshot(dataDir:string):Promise<HostSnapshot>{
  const [memory,pressure,disk,gpus]=await Promise.all([
    readFile('/proc/meminfo','utf8').then(parseMemory).catch(()=>null),
    readFile('/proc/pressure/memory','utf8').then(parsePressure).catch(()=>null),
    statfs(dataDir).then(s=>({total:s.blocks*s.bsize,available:s.bavail*s.bsize})).catch(()=>null),
    run('/usr/bin/nvidia-smi',['--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu','--format=csv,noheader,nounits'],{timeout:2000,maxBuffer:32768,env:{PATH:'/usr/bin:/bin',LANG:'C'}}).then(r=>parseGPUs(r.stdout)).catch(()=>null)
  ]);
  return {hostname:os.hostname(),platform:`${os.type()} ${os.release()}`,memory,pressure,disk,gpus};
}
export function parseHealth(v:unknown):KernelHealth{
  const h=v as Record<string,any>;
  if(!h||typeof h.ready!=='boolean'||typeof h.model!=='string')throw Error('Invalid health');
  return {ready:h.ready,model:h.model,engine:typeof h.engine==='string'?h.engine:'unknown',spec:typeof h.spec==='boolean'?h.spec:null,store:typeof h.kvstore?.status==='string'?h.kvstore.status:'unknown',
    readout:typeof h.readout?.supported==='boolean'?h.readout.supported:null,assets:Array.isArray(h.readout?.assets)&&h.readout.assets.every((x:unknown)=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x))?h.readout.assets:null,steering:typeof h.readout?.steering==='boolean'?h.readout.steering:null};
}
type Dependencies={endpoint:string|undefined;busy:()=>boolean;host:()=>Promise<HostSnapshot>;fetcher?:typeof fetch;now?:()=>number};
/** Process-local single flights and a 65 s cache cooldown even after a timeout:
 * the upstream handler may keep waiting for its engine lock after disconnect.
 * There is no periodic polling and no queue. External kernel clients are unknown.
 */
export function systemProbe(d:Dependencies){
  const now=d.now??Date.now,fetcher=d.fetcher??fetch;
  let stats:KVStats|null=null,at:string|null=null,next=0,inFlight=false;
  let light:Promise<{health:KernelHealth|null;healthError:string|null;host:HostSnapshot|null}>|undefined,lightAt=-Infinity;
  let base:string|null=null;
  try{const u=new URL(d.endpoint??'');if(u.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(u.hostname)&&!u.username&&!u.password&&u.pathname==='/'&&!u.search&&!u.hash)base=u.origin;}catch{/* unavailable */}
  async function json(path:string){if(!base)throw Error('No endpoint');const r=await fetcher(base+path,{signal:AbortSignal.timeout(5000),redirect:'error'});if(!r.ok)throw Error('Unavailable');return r.json();}
  function snapshot(){
    if(!light||now()-lightAt>=10000){lightAt=now();light=Promise.all([
      json('/health').then(parseHealth).then(health=>({health,healthError:null})).catch(()=>({health:null,healthError:base?'Kernel health unavailable.':'No valid loopback kernel endpoint configured.'})),
      d.host().catch(()=>null)
    ]).then(([h,host])=>({...h,host}));}
    return light;
  }
  return {async get(refreshCache=false):Promise<SystemSnapshot>{
    let state:SystemSnapshot['cache']['state']='idle',message=stats?'Last successful cache sample.':'Cache counters have not been requested.';
    if(refreshCache){
      if(d.busy()||inFlight){state='busy';message='Cache refresh deferred: a local Pi run or another refresh is active.';}
      else if(now()<next){state='cooldown';message='Cache refresh is limited to once every 65 seconds, including failed requests.';}
      else{
        inFlight=true;next=now()+65000;
        try{stats=readStats(await json('/v1/kvstore/stats'));at=new Date(now()).toISOString();state='fresh';message='Kernel-wide counters, cumulative since the kernel reset them.';}
        catch{state='unavailable';message='Cache refresh unavailable; the engine may be busy. Any previous sample is retained, not replaced with zeros.';}
        finally{inFlight=false;}
      }
    }
    const s=await snapshot();
    return {at:new Date(lightAt).toISOString(),endpoint:base,...s,cache:{stats,at,state,message,nextRefreshAt:next?new Date(next).toISOString():null}};
  }};
}
