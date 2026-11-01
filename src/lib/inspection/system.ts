import type {KVStats} from '../live.ts';
export type KernelHealth={ready:boolean;model:string;engine:string;spec:boolean|null;store:string;readout:boolean|null;assets:string[]|null;steering:boolean|null};
export type HostSnapshot={hostname:string;platform:string;memory:{total:number;available:number;swapTotal:number;swapFree:number}|null;
  pressure:{some:number|null;full:number|null}|null;disk:{total:number;available:number}|null;
  gpus:{name:string;totalMiB:number;usedMiB:number;utilization:number|null;temperature:number|null}[]|null};
export type SystemSnapshot={at:string;endpoint:string|null;health:KernelHealth|null;healthError:string|null;host:HostSnapshot|null;
  cache:{stats:KVStats|null;at:string|null;state:'idle'|'fresh'|'busy'|'cooldown'|'unavailable';message:string;nextRefreshAt:string|null}};
export function bytes(v:number|null|undefined){
  if(v===null||v===undefined||!Number.isFinite(v))return 'Unavailable';
  if(v===0)return '0 B';const i=Math.min(4,Math.max(0,Math.floor(Math.log(v)/Math.log(1024))));
  return `${(v/1024**i).toFixed(i===0?0:1)} ${['B','KiB','MiB','GiB','TiB'][i]}`;
}
export function fraction(n:number,d:number){return d>0?`${(n/d*100).toFixed(1)}%`:'Not defined';}
