import {projection,type TurnView} from '../pi/model.ts';

export type Channel='reasoning'|'answer';
export function turnCounts(turn:TurnView){
  const rows=turn.records;
  return {rows:rows.length,prompt:rows.filter(r=>r.source==='prompt').length,
    reasoning:rows.filter(r=>r.source==='generated'&&r.channel==='reasoning').length,
    answer:rows.filter(r=>r.source==='generated'&&r.channel==='answer').length,
    other:rows.filter(r=>r.source==='generated'&&!['reasoning','answer'].includes(r.channel??'')).length,
    emotion:rows.filter(r=>r.emotion!==null).length,workspace:rows.filter(r=>r.workspace!==null).length,
    reused:turn.summary?turn.summary.ranges.reused[1]-turn.summary.ranges.reused[0]:null};
}
export function directionSeries(turn:TurnView,layer:number,name:string,channel:Channel){
  return turn.records.filter(r=>r.source==='generated'&&r.channel===channel).map(r=>({pos:r.pos,value:projection(turn,r,layer,name)}));
}
export function directionMeans(turn:TurnView,layer:number,channel:Channel){
  return (turn.header?.settings?.emotion.names??[]).map(name=>{
    const points=directionSeries(turn,layer,name,channel);let n=0,mean=0;
    for(const p of points)if(p.value!==null){n++;mean+=(p.value-mean)/n;}
    return {name,mean:n?mean:null,defined:n,missing:points.length-n,total:points.length};
  }).sort((a,b)=>(b.mean===null?-Infinity:Math.abs(b.mean))-(a.mean===null?-Infinity:Math.abs(a.mean))||a.name.localeCompare(b.name));
}
export function steeringText(turn:TurnView){
  if(!turn.header)return 'Unknown';
  const s=turn.header.steer;
  return s?`${s.direction} ${s.strength>=0?'+':''}${s.strength} · L${s.layer} · from position ${s.from_pos}`:'Off';
}
export function workspaceCoverage(turn:TurnView,layer:number,labels:Record<string,string|null>){
  const li=turn.header?.settings?.workspace?.layers.indexOf(layer)??-1;
  const rows=turn.records.filter(r=>r.source==='generated'&&r.channel==='reasoning');
  const defined=rows.filter(r=>li>=0&&r.workspace?.top[li]?.every(([id])=>typeof labels[id]==='string')).length;
  return {defined,unknown:rows.length-defined,total:rows.length};
}
/** A gap or an undefined row starts a new path. Never draw through unobserved positions. */
export function trajectory(points:{pos:number;value:number|null}[]){
  const defined=points.filter((p):p is {pos:number;value:number}=>p.value!==null);
  const first=points[0]?.pos??0,last=points.at(-1)?.pos??first;
  const extent=defined.reduce((max,p)=>Math.max(max,Math.abs(p.value)),1),paths:string[]=[];
  let segment='',prior:number|null=null;
  for(const p of points){
    if(p.value===null){if(segment)paths.push(segment);segment='';prior=null;continue;}
    if(prior!==null&&p.pos!==prior+1){if(segment)paths.push(segment);segment='';}
    const x=24+((p.pos-first)/Math.max(1,last-first))*752,y=105-p.value/extent*80;
    segment+=`${segment?'L':'M'}${x.toFixed(2)},${y.toFixed(2)} `;prior=p.pos;
  }
  if(segment)paths.push(segment);
  return {paths,first,last,extent,defined:defined.length,total:points.length};
}
