import {projection,type PiView,type TurnView} from './model.ts';

export const SEED_WATCH=[' can\'t',' cannot',' couldn\'t',' hallucin',' invent'];
export type AlertRules={emotion:{enabled:boolean;name:string;layer:number;threshold:number;side:'above'|'below'};
  workspace:{enabled:boolean;layer:number;prefixes:string[]}};
export const defaultRules=():AlertRules=>({emotion:{enabled:false,name:'afraid',layer:29,threshold:5,side:'above'},workspace:{enabled:true,layer:44,prefixes:[...SEED_WATCH]}});
export type SignalAlert={id:string;request:string;turn:number;pos:number;token:number;kind:'emotion'|'workspace';rule:string;detail:string};
export type AlertResult={events:SignalAlert[];total:number;counts:Record<string,number>;baselineSamples:number;unknownWorkspaceRows:number};
const fold=(s:string)=>s.trim().toLowerCase();
export function validRules(r:AlertRules){
  if(typeof r.emotion.enabled!=='boolean'||typeof r.workspace.enabled!=='boolean'||!['above','below'].includes(r.emotion.side)
    ||!Number.isInteger(r.emotion.layer)||!Number.isInteger(r.workspace.layer)||r.emotion.layer<0||r.emotion.layer>51||r.workspace.layer<0||r.workspace.layer>51
    ||typeof r.emotion.name!=='string'||!r.emotion.name||!Number.isFinite(r.emotion.threshold)||r.emotion.threshold<=0
    ||!Array.isArray(r.workspace.prefixes)||r.workspace.prefixes.length>32||r.workspace.prefixes.some(s=>typeof s!=='string'||!fold(s)||s.length>64))throw Error('Invalid signal-alert rule. Use a positive threshold and up to 32 nonempty token prefixes.');
}

/** Pure replay over authoritative, deduplicated records. It never reads text or infers a missing row.
 * Baseline: all earlier defined generated reasoning projections in this conversation,
 * for the same asset + named direction + layer. 32 earlier values warm up the mean.
 * Crossing: two contiguous observed reasoning positions with eligible thresholds,
 * below then at/above the selected signed difference. A gap resets crossing history,
 * not the running mean. The current sample joins the mean only AFTER its test.
 */
export function evaluateAlerts(state:Pick<PiView,'turns'>,rules:AlertRules,labels:Record<string,string|null>):AlertResult{
  validRules(rules);
  const out:AlertResult={events:[],total:0,counts:{},baselineSamples:0,unknownWorkspaceRows:0};
  const means=new Map<string,{n:number;mean:number}>();
  function emit(a:SignalAlert){out.total++;out.counts[a.request]=(out.counts[a.request]??0)+1;out.events.push(a);if(out.events.length>200)out.events.shift();}
  const unique=new Map<string,TurnView>();for(const t of state.turns)unique.set(t.request,t);
  for(const turn of [...unique.values()].sort((a,b)=>a.turn-b.turn)){
    const emotion=turn.header?.settings?.emotion,workspace=turn.header?.settings?.workspace;
    const key=emotion?`${emotion.directions}:${rules.emotion.layer}:${rules.emotion.name}`:null;
    let priorPos:number|null=null,priorAbove:boolean|null=null,priorWords:Set<string>|null=null;
    for(const row of [...new Map(turn.records.map(r=>[r.pos,r])).values()].sort((a,b)=>a.pos-b.pos)){
      if(row.source!=='generated'||row.channel!=='reasoning'){priorPos=null;priorAbove=null;priorWords=null;continue;}
      const contiguous=priorPos!==null&&row.pos===priorPos+1;
      if(!contiguous){priorAbove=null;priorWords=null;}priorPos=row.pos;
      if(rules.emotion.enabled&&key){
        const value=projection(turn,row,rules.emotion.layer,rules.emotion.name);
        if(value===null){priorAbove=null;}else{
          const b=means.get(key)??{n:0,mean:0};
          if(b.n>=32){const delta=value-b.mean,above=(rules.emotion.side==='above'?delta:-delta)>=rules.emotion.threshold;
            if(above&&priorAbove===false)emit({id:`${turn.request}:${row.pos}:emotion`,request:turn.request,turn:turn.turn,pos:row.pos,token:row.token,kind:'emotion',
              rule:`${rules.emotion.name} L${rules.emotion.layer} ${rules.emotion.side} prior mean by ${rules.emotion.threshold}`,
              detail:`Projection ${value.toFixed(4)}; prior mean ${b.mean.toFixed(4)} from ${b.n} earlier reasoning positions; difference ${delta.toFixed(4)}.`});
            priorAbove=above;
          }else priorAbove=null;
          b.n++;b.mean+=(value-b.mean)/b.n;means.set(key,b);out.baselineSamples++;
        }
      }
      if(rules.workspace.enabled){
        const li=workspace?.layers.indexOf(rules.workspace.layer)??-1,top=li<0?null:row.workspace?.top[li];
        // Incomplete labels are an unknown set, never negative evidence for a watch word.
        if(!top||top.some(([id])=>typeof labels[id]!=='string')){priorWords=null;out.unknownWorkspaceRows++;continue;}
        const hits=new Map<string,{id:number;label:string;rank:number}>();
        for(const prefix of new Set(rules.workspace.prefixes.map(fold))){const rank=top.findIndex(([id])=>fold(labels[id]!).startsWith(prefix));if(rank>=0)hits.set(prefix,{id:top[rank][0],label:labels[top[rank][0]]!,rank:rank+1});}
        if(priorWords)for(const [prefix,hit]of hits)if(!priorWords.has(prefix))emit({id:`${turn.request}:${row.pos}:workspace:${prefix}`,request:turn.request,turn:turn.turn,pos:row.pos,token:row.token,kind:'workspace',
          rule:`Workspace L${rules.workspace.layer}: ${JSON.stringify(prefix)} entered top-${workspace!.k}`,
          detail:`Isolated token ${JSON.stringify(hit.label)} (#${hit.id}), rank ${hit.rank} within the supplied top-k; case-folded token-prefix match, not a semantic judgement.`});
        priorWords=new Set(hits.keys());
      }
    }
  }
  return out;
}
