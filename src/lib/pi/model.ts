import type {AssistantMessage, Message} from '@earendil-works/pi-ai';
import type {BridgeMessage, BridgeSnapshot} from 'glimmer-agent/bridge';
import type {AgentEvent, EntryRecord} from 'glimmer-agent/pi';
import type {ReadoutBatch, ReadoutTurnRecord, SettingsMessage} from 'glimmer-agent/extension';
import type {ReadoutHeader, ReadoutRecord} from 'glimmer-agent/contract';

export type Assets = {
  supported:boolean; error:string|null;
  directions:{id:string; names:string[]; layers:number[]}[];
  lenses:{id:string; layers:number[]}[];
  tokenizer:string|null;
};
export type TurnView = {request:string; turn:number; header:ReadoutHeader|null; records:ReadoutRecord[];
  status:'streaming'|ReadoutTurnRecord['status']; error:string|null; summary:ReadoutTurnRecord['summary']};
export type PiView = {entries:EntryRecord[]; partial:AssistantMessage|null; busy:boolean; settings:SettingsMessage|null;
  turns:TurnView[]; errors:string[]; connected:boolean};
export type Wire = {type:'bridge.snapshot'; snapshot:BridgeSnapshot}|BridgeMessage;
export const emptyPi = ():PiView=>({entries:[],partial:null,busy:false,settings:null,turns:[],errors:[],connected:false});

function putEntry(state:PiView, entry:EntryRecord) {
  state.entries=[...state.entries.filter(e=>e.id!==entry.id),entry].sort((a,b)=>a.id-b.id);
}
function batch(state:PiView, b:ReadoutBatch) {
  let t=state.turns.find(t=>t.request===b.request);
  if(t && t.status!=='streaming')return; // a terminal record already owns the complete list
  if(!t){t={request:b.request,turn:b.turn,header:b.header,records:[],status:'streaming',error:null,summary:null};state.turns.push(t);}
  const positions=new Map(t.records.map(r=>[r.pos,r]));
  for(const r of b.records){
    const prior=positions.get(r.pos);
    if(prior && JSON.stringify(prior)!==JSON.stringify(r))throw Error('Conflicting duplicate readout position.');
    positions.set(r.pos,r);
  }
  t.records=[...positions.values()].sort((a,b)=>a.pos-b.pos);
}
function terminal(state:PiView,t:ReadoutTurnRecord){
  state.turns=[...state.turns.filter(v=>v.request!==t.request),{request:t.request,turn:t.turn,header:t.header,
    records:t.records,status:t.status,error:t.error,summary:t.summary}].sort((a,b)=>a.turn-b.turn);
}
function entryReadout(state:PiView,e:EntryRecord){
  if(e.kind==='glimmer.readout.batch')batch(state,e.data as ReadoutBatch);
  if(e.kind==='glimmer.readout.turn')terminal(state,e.data as ReadoutTurnRecord);
}
function pi(state:PiView,e:AgentEvent){
  if(e.type==='snapshot'){
    state.entries=[...e.entries];state.partial=e.generation?.message?structuredClone(e.generation.message):null;
    state.busy=Boolean(e.run||e.generation||e.inbox.length);state.turns=[];
    for(const entry of state.entries)entryReadout(state,entry);
  }else if(e.type==='run_start')state.busy=true;
  else if(e.type==='run_end')state.busy=false;
  else if(e.type==='message_start' && e.message.role==='assistant')state.partial=structuredClone(e.message);
  else if(e.type==='message_update'){
    for(const c of e.changes){
      if(c.type==='message'){state.partial=structuredClone(c.message);continue;}
      if(!state.partial)throw Error('Pi supplied a message delta without a message.');
      if(c.type==='text_start'||c.type==='thinking_start'||c.type==='toolcall_start'||c.type==='block')state.partial.content[c.contentIndex]=structuredClone(c.block);
      else if(c.type==='text_delta'||c.type==='thinking_delta'){
        const b=state.partial.content[c.contentIndex];
        if(c.type==='text_delta' && b?.type==='text')b.text+=c.delta;
        else if(c.type==='thinking_delta' && b?.type==='thinking')b.thinking+=c.delta;
        else throw Error('Pi delta does not match its content block.');
      }
    }
  }else if(e.type==='message_end'){putEntry(state,e.entry);state.partial=null;}
  else if(e.type==='entry_appended'){putEntry(state,e.entry);entryReadout(state,e.entry);}
  else if(e.type==='task_failed')state.errors.push(e.message);
  else if(e.type==='auto_retry_start')state.errors.push(`Retry ${e.attempt}: ${e.errorMessage}`);
}

/** Called in wire order. Snapshots replace, batches append by request+position, terminals replace (never double count). */
export function reducePi(previous:PiView,wire:Wire):PiView {
  // Do not deep-copy every historic position on every text delta.
  const state={...previous,entries:[...previous.entries],turns:previous.turns.map(t=>({...t})),errors:[...previous.errors],
    partial:previous.partial?structuredClone(previous.partial):null};
  if(wire.type==='bridge.snapshot'){
    pi(state,wire.snapshot.pi);state.settings=wire.snapshot.settings;state.connected=true;
  }else if(wire.type==='pi')pi(state,wire.event);
  else if(wire.type==='settings'){
    if(!state.settings || wire.version>state.settings.version)state.settings=wire;
  }else if(wire.type==='readout.batch')batch(state,wire.batch);
  else if(wire.type==='readout.turn')terminal(state,wire.turn);
  else if(wire.type==='readout.error')state.errors.push(wire.error);
  else throw Error('Unknown bridge message.');
  state.errors=state.errors.slice(-20);
  return state;
}

export function textOf(message:Message,kind:'text'|'thinking'='text'):string {
  if(typeof message.content==='string')return kind==='text'?message.content:'';
  return message.content.map(b=>b.type==='text'&&kind==='text'?b.text:b.type==='thinking'&&kind==='thinking'?b.thinking:'').join('');
}
export function messagesOf(state:PiView){
  const entries=state.entries.flatMap(e=>(e.model??[]).filter(m=>m.role==='user'||m.role==='assistant').map((m,i)=>({id:`${e.id}:${i}`,message:m,streaming:false})));
  if(state.partial)entries.push({id:'partial',message:state.partial,streaming:true});
  return entries;
}
export function lastRow(state:PiView){
  const turn=state.turns.at(-1);const record=turn?.records.findLast(r=>r.source==='generated');
  return turn&&record?{turn,record}:null;
}
export function projection(turn:TurnView,row:ReadoutRecord,layer:number,name:string):number|null {
  const setting=turn.header?.settings?.emotion;
  if(!setting||!row.emotion)return null;
  const value=row.emotion.proj[setting.layers.indexOf(layer)]?.[setting.names.indexOf(name)];
  return typeof value==='number'&&Number.isFinite(value)?value:null;
}
export function selectedRow(state:PiView,selection?:{request:string;pos:number}){
  if(!selection)return lastRow(state);
  const turn=state.turns.find(t=>t.request===selection.request),record=turn?.records.find(r=>r.pos===selection.pos);
  return turn&&record?{turn,record}:null;
}
