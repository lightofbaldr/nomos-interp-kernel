import type {ReadoutRecord} from 'glimmer-agent/contract';
import {textOf,type TurnView,type PiView} from '../pi/model.ts';
import {answerWindows,type Step2Info} from './step2.ts';

export type WorkspaceTurn = {
  id:string; title:string; prompt:string; source:'recorded'|'live'; layers:number[]; k:number|null;
  records:ReadoutRecord[]; labels:Record<string,string|null>; steering:string;
  provenance:{label:string; sha256?:string; tokenizer?:string; schedule?:string};
  step2?:Step2Info;
};
export type Candidate = {id:number;score:number;rank:number;label:string|null;hiddenReason:string|null};

/** A display heuristic, NOT a lexical or scientific classification. Never edits the raw list. */
export function filterReason(label:string|null):string|null {
  if(label===null)return null; // Unknown labels must remain visible as IDs.
  if(/<\|[^]*?\|>|<atem:|\uFFFD/.test(label))return 'special token or partial character';
  if(!/\p{L}/u.test(label))return 'punctuation, spacing or number';
  if(!/^\s[\p{L}\p{M}][\p{L}\p{M}'’\-]+$/u.test(label))return 'possible word fragment';
  return null;
}
export function candidates(turn:WorkspaceTurn,row:ReadoutRecord|undefined,layer:number):Candidate[]|null {
  const at=turn.layers.indexOf(layer),top=at<0?null:row?.workspace?.top[at];
  if(!top)return null;
  return top.map(([id,score],i)=>{const label=turn.labels[id]??null;return {id,score,rank:i+1,label,hiddenReason:filterReason(label)};});
}
export function fromLive(turn:TurnView,labels:WorkspaceTurn['labels'],prompt:string):WorkspaceTurn {
  const w=turn.header?.settings?.workspace,s=turn.header?.steer;
  return {id:turn.request,title:`Turn ${turn.turn+1}`,prompt,source:'live',layers:w?.layers??[],k:w?.k??null,
    records:turn.records,labels,steering:!turn.header?'Steering not recorded':s?`${s.direction} ${s.strength>=0?'+':''}${s.strength} · L${s.layer}`:'Steering off',
    provenance:{label:`Saved Pi turn · ${turn.status}`,schedule:'Consumed-position readouts. Text deltas are not tokens.'}};
}
/** Resolve this request's prompt from its durable boundary, not the first/latest conversation message. */
export function promptForTurn(pi:PiView,turn:TurnView):string {
  const boundary=pi.entries.find(e=>['glimmer.readout.turn','glimmer.readout.batch'].includes(e.kind)&&(e.data as {request?:string})?.request===turn.request);
  // Live readout messages carry their durable entry ID but do not necessarily
  // also arrive as pi.entry_appended. Keep that real boundary rather than
  // inventing an entry or borrowing the latest conversation prompt.
  const boundaryId=boundary?.id??turn.entryId;
  if(boundaryId===undefined)return ''; // Before the first persisted batch.
  const prompts=pi.entries.filter(e=>e.id<boundaryId).sort((a,b)=>a.id-b.id)
    .flatMap(e=>(e.model??[]).filter(m=>m.role==='user'));
  return prompts.length?textOf(prompts[prompts.length-1]):'';
}
export function selectedPosition(turn:WorkspaceTurn,position:number|null,channel:string):ReadoutRecord|undefined {
  const selected=turn.records.find(r=>r.pos===position);
  if(selected&&(channel==='all'||selected.channel===channel))return selected;
  return turn.records.find(r=>r.source==='generated'&&(channel==='all'||r.channel===channel)&&r.workspace)
    ??turn.records.find(r=>channel==='all'||r.channel===channel);
}
export function validateWorkspaceTurn(raw:unknown):WorkspaceTurn {
  const t=raw as WorkspaceTurn,fail=()=>{throw Error('Invalid recorded workspace data.');};
  if(!t||typeof t.id!=='string'||typeof t.title!=='string'||typeof t.prompt!=='string'||t.source!=='recorded'
    ||!Array.isArray(t.layers)||new Set(t.layers).size!==t.layers.length||!t.layers.every(l=>Number.isInteger(l)&&l>=0&&l<100)
    ||t.k!==null&&(!Number.isInteger(t.k)||t.k<1||t.k>128)||!t.labels||typeof t.labels!=='object'
    ||!Object.values(t.labels).every(s=>s===null||typeof s==='string')||!Array.isArray(t.records)||t.records.length>50000
    ||!t.provenance||typeof t.provenance.label!=='string'||typeof t.steering!=='string')fail();
  let last=-1;
  for(const r of t.records){
    if(!Number.isSafeInteger(r.pos)||r.pos<=last||!Number.isSafeInteger(r.token)||r.token<0
      ||!['prompt','generated'].includes(r.source)||!(r.channel===null||typeof r.channel==='string')||!Array.isArray(r.missing))fail();
    last=r.pos;
    if(r.workspace!==null){
      if(!Array.isArray(r.workspace?.top)||r.workspace.top.length!==t.layers.length)fail();
      for(const top of r.workspace.top){
        if(!Array.isArray(top)||top.length!==t.k||new Set(top.map(p=>p[0])).size!==top.length)fail();
        let previous=Infinity;
        for(const p of top){if(!Array.isArray(p)||p.length!==2||!Number.isSafeInteger(p[0])||p[0]<0||!Number.isFinite(p[1])||p[1]>previous)fail();previous=p[1];}
      }
    }
  }
  if(t.step2){
    const m=t.step2.marker,w=answerWindows(t.records,t.step2);
    if(m.postHoc!==true||m.notDecisionTime!==true||!Number.isSafeInteger(m.answerTokenIndex)||m.answerTokenIndex<0
      ||!Number.isSafeInteger(m.charOffset)||m.charOffset<0||m.answerTokens!==w.answerCount||w.taskStart===null)fail();
  }
  return t;
}
