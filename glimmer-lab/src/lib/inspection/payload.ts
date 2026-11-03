import type {Annotation} from '../pi/annotation.ts';
const number=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v:null;
const text=(v:unknown)=>typeof v==='string'?v:null;
/** Display the saved payload's scalars, without recomputing signals or adopting
 * its prose as instructions. Old payloads may not contain raw means.
 */
export function payloadReading(a:Annotation|undefined){
  if(!a)return null;
  try{
    const p=JSON.parse(a.payload_json),msg=p.messages?.find((m:any)=>m.role==='user');
    const s=JSON.parse(msg.content);if(s.schema!=='glimmer.turn-signals/1')return null;
    const excerpt=(v:any)=>({text:text(v?.text),characters:number(v?.characters),truncated:typeof v?.truncated==='boolean'?v.truncated:null});
    const sourceLayers:any[]=Array.isArray(s.emotion?.layers)?s.emotion.layers:[];
    const layers=sourceLayers.filter((l:any)=>Number.isInteger(l?.layer)).map((l:any)=>({
      layer:l.layer as number,channels:(['reasoning','answer'] as const).map(channel=>{
        const c=l[channel],rows=(Array.isArray(c?.moves)?c.moves:[]).filter((m:any)=>typeof m?.name==='string').map((m:any)=>({name:m.name as string,raw:number(m.raw_mean_projection),delta:number(m.mean_delta),defined:number(m.raw_defined),differenceDefined:number(m.defined),missing:number(m.missing)}));
        const steered=l.steered_direction;
        if(typeof steered?.name==='string'&&!rows.some((r:any)=>r.name===steered.name))rows.push({name:steered.name,raw:number(steered[channel]?.raw_mean_projection),delta:null,defined:number(steered[channel]?.defined),differenceDefined:null,missing:number(steered[channel]?.missing)});
        return {channel,rows,total:number(c?.rows),status:text(c?.status)};
      })
    }));
    return {prompt:excerpt(s.text?.prompt),reasoning:excerpt(s.text?.reasoning),answer:excerpt(s.text?.answer),layers,
      steering:s.steering?.status==='none'?'Off':typeof s.steering?.direction==='string'&&number(s.steering?.strength)!==null?`${s.steering.direction} ${s.steering.strength>=0?'+':''}${s.steering.strength} · L${s.steering.layer}`:'Unknown',
      statistic:text(s.emotion?.statistic),rawStatistic:text(s.emotion?.raw_statistic)};
  }catch{return null;}
}
