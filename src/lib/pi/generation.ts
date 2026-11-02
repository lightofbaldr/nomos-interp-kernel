import type {EntryRecord} from 'glimmer-agent/pi';
import type {Message} from '@earendil-works/pi-ai';
import type {AgentBudgetAdmission} from '../agent/budget';
import type {TurnView} from './model';

export const REPLY_CAP=6144;
export const CHAT_TIMEOUT_MS=600000;
export type GenerationSettings={version:number;sampling:boolean};
export type GenerationParameters={temperature:number;top_p:number;top_k:number;max_tokens:number};
export type GenerationUsed={task:number;attempt:number;cutoff:number;request:string|null;mode:'greedy'|'sampled';parameters:GenerationParameters;budget?:AgentBudgetAdmission;advertisedContextTokens?:number};
export function generationParameters(sampling:boolean):GenerationParameters {
  if(typeof sampling!=='boolean')throw Error('Sampling must be a boolean.');
  return sampling?{temperature:1,top_p:0.95,top_k:64,max_tokens:REPLY_CAP}:{temperature:0,top_p:1,top_k:0,max_tokens:REPLY_CAP};
}
export function generationSettings(entries:readonly EntryRecord[]):GenerationSettings|null {
  const e=entries.findLast(e=>e.kind==='glimmer.generation.changed'),s=e?.data as GenerationSettings|undefined;
  return s&&Number.isSafeInteger(s.version)&&typeof s.sampling==='boolean'?s:null;
}
export function generationFor(entries:readonly EntryRecord[],task:number|undefined|null,request?:string,beforeEntry?:number):GenerationUsed|null {
  if(task==null)return null;
  return entries.findLast(e=>e.kind==='glimmer.generation.used'&&(e.data as GenerationUsed)?.task===task&&(!request||(e.data as GenerationUsed).request===request)&&(beforeEntry===undefined||e.id<beforeEntry))?.data as GenerationUsed??null;
}
export function generationLabel(used:GenerationUsed|null|undefined):string {
  return used?`${used.mode==='sampled'?"Glimmer's sampling":'Greedy'} · T ${used.parameters.temperature} / p ${used.parameters.top_p} / k ${used.parameters.top_k} · ${used.parameters.max_tokens.toLocaleString('en-US')}-token ${used.budget?'requested ':''}limit`:'Decoding not recorded for this turn';
}
/** Derived, not returned by the serve. Never borrow a different request or use
 * a partial summary. The advertised capacity is not a runtime attestation. */
export function effectiveReplyBudget(used:GenerationUsed|null|undefined,turn:TurnView|undefined|null){
  const capacity=used?.advertisedContextTokens,prompt=turn?.summary?.ranges.generated[0],requested=used?.parameters.max_tokens;
  if(!used||!turn||turn.request!==used.request||turn.status!=='complete'||!turn.header?.settings||!Number.isSafeInteger(capacity)||!Number.isSafeInteger(prompt)||!Number.isSafeInteger(requested)||capacity!<1||prompt!<1||requested!<1||prompt!>=capacity!)return null;
  const effective=Math.min(requested!,capacity!-prompt!-1);
  return {requested:requested!,prompt:prompt!,capacity:capacity!,effective,clamped:effective<requested!,basis:'saved prompt boundary + advertised capacity; not a returned serve limit'};
}
export function cappedReasoningOnly(message:Message):boolean {
  return message.role==='assistant'&&message.stopReason==='length'
    &&message.content.some(b=>b.type==='thinking'&&b.thinking.trim().length>0)
    &&!message.content.some(b=>b.type==='toolCall'||b.type==='text'&&b.text.trim().length>0);
}
export function wasRetried(entries:readonly EntryRecord[],id:number):boolean {
  return entries.some(e=>e.kind==='glimmer.retry'&&(e.data as {target?:number})?.target===id);
}
export function retryTarget(entries:readonly EntryRecord[]):number|null {
  const e=entries.findLast(e=>e.model?.length);
  return e?.model?.length===1&&cappedReasoningOnly(e.model[0])&&!wasRetried(entries,e.id)?e.id:null;
}
