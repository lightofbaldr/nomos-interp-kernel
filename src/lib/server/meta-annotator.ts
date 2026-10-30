import {createHash} from 'node:crypto';
import {BACKGROUND_CONTEXT as ctx,defineDoc,type ConversationId,type Harness} from 'glimmer-agent/pi';
import type {ReadoutTurnRecord} from 'glimmer-agent/extension';
import {annotationFor,ANNOTATION_KIND,type Annotation} from '../pi/annotation.ts';
import {validRules,type AlertRules} from '../pi/alerts.ts';
import {resolveTurn,summariseTurn} from './meta-summary.ts';
import {META_MODEL,MetaError,makePayload,readMetaKey,requestNote} from './meta-client.ts';

const State=defineDoc<{lastAt:number|null;claims:Record<string,boolean>}>({kind:'glimmer.meta.admission',version:1,scope:'conversation',history:'latest',fork:'initial',initial:()=>({lastAt:null,claims:{}})});
type HarnessInstance=Awaited<ReturnType<typeof Harness.open>>;
type Options={key?:()=>Promise<string|null>;labels:(turn:ReadoutTurnRecord)=>Promise<{labels:Record<string,string|null>;tokenizer:string|null}>;
  now?:()=>number;call?:(payload:string,key:string)=>Promise<string>};

/** One-shot, explicitly admitted work. Pending entries survive a crash but never trigger a retry. */
export function createAnnotator(harness:HarnessInstance,options:Options){
  const getKey=options.key??readMetaKey,now=options.now??Date.now;
  return {
    async available(){const available=Boolean(await getKey());return {available,model:META_MODEL,message:available?null:new MetaError('missing').message};},
    async annotate(id:string,request:string,rules:AlertRules):Promise<Annotation>{
      if(!/^[1-9]\d{0,14}$/.test(id)||!Number.isSafeInteger(Number(id))||!new RegExp('^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$').test(request))throw Error('Invalid annotation target.');
      validRules(rules);
      const chat=await harness.conversation(Number(id) as ConversationId,ctx);if(!chat)throw Error('Conversation not found.');
      const entries=(await chat.context(ctx)).entries,existing=annotationFor(entries,request);if(existing)return existing;
      const target=resolveTurn(entries,request),key=await getKey();if(!key)throw new MetaError('missing');
      const labels=await options.labels(target.turn),summary=summariseTurn(entries,request,rules,labels.labels,labels.tokenizer),payload=makePayload(summary);
      // Reject before persisting a request too, not just before its network send.
      if(payload.includes(key))throw new MetaError('payload');
      const started=now(),record:Annotation={v:1,request,turn:target.turn.turn,model:META_MODEL,status:'pending',started_at:new Date(started).toISOString(),finished_at:null,
        payload_json:payload,payload_sha256:createHash('sha256').update(payload).digest('hex'),note:null,error:null};
      const admitted=await chat.commit(async tx=>{
        const state=await tx.doc(State,chat.id);
        if(Object.hasOwn(state.claims,request))return false;
        if(state.lastAt!==null&&started-state.lastAt<60000)throw Error('Muse Wonder has a 60-second cooldown per conversation.');
        if(Object.keys(state.claims).length>=1000)throw Error('Conversation annotation limit reached.');
        state.claims[request]=true;state.lastAt=started;
        await tx.appendEntry(chat.id,{kind:ANNOTATION_KIND,data:record});return true;
      },ctx);
      if(!admitted){const saved=annotationFor((await chat.context(ctx)).entries,request);if(!saved)throw Error('Annotation admission is unresolved; it will not be retried.');return saved;}
      let completed:Annotation;
      try{const note=await(options.call??requestNote)(payload,key);completed={...record,status:'complete',finished_at:new Date(now()).toISOString(),note};}
      catch(e){completed={...record,status:'failed',finished_at:new Date(now()).toISOString(),error:e instanceof MetaError?new MetaError(e.code).message:new MetaError('transport').message};}
      await chat.commit(async tx=>{await tx.appendEntry(chat.id,{kind:ANNOTATION_KIND,data:completed});},ctx);
      return completed;
    }
  };
}
