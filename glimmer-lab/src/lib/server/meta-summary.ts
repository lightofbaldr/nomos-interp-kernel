import type {EntryRecord} from 'glimmer-agent/pi';
import type {ReadoutTurnRecord} from 'glimmer-agent/extension';
import {textOf,projection} from '../pi/model.ts';
import {evaluateAlerts,validRules,type AlertRules} from '../pi/alerts.ts';

/** Use the task identity, never the latest answer or current steering controls. */
export function resolveTurn(entries:readonly EntryRecord[],request:string){
  const hits=entries.filter(e=>e.kind==='glimmer.readout.turn'&&(e.data as ReadoutTurnRecord)?.request===request);
  if(hits.length!==1)throw Error('One saved turn is required.');
  const entry=hits[0],turn=entry.data as ReadoutTurnRecord;
  if(turn.status!=='complete')throw Error('Only completed turns can be annotated.');
  const replies=entries.flatMap(e=>e.byTaskId===turn.task?(e.model??[]).filter(m=>m.role==='assistant').map(m=>({entry:e,message:m})):[]);
  if(replies.length!==1||replies[0].entry.id<=entry.id)throw Error('The completed answer is not yet saved, or its task is ambiguous.');
  const reply=replies[0],message=reply.message;
  if(message.role!=='assistant'||!['stop','length'].includes(message.stopReason))throw Error('The saved answer did not finish successfully.');
  const prompts=entries.filter(e=>e.id<entry.id).sort((a,b)=>a.id-b.id).flatMap(e=>(e.model??[]).filter(m=>m.role==='user').map(m=>({entry:e,message:m})));
  const prompt=prompts.at(-1);if(!prompt)throw Error('The source prompt is missing.');
  return {turn,turnEntry:entry.id,promptEntry:prompt.entry.id,responseEntry:reply.entry.id,
    prompt:textOf(prompt.message),reasoning:textOf(message,'thinking'),answer:textOf(message),stopReason:message.stopReason};
}
function excerpt(text:string,limit:number){
  const truncated=text.length>limit,marker='\n[…truncated…]\n',half=Math.floor((limit-marker.length)/2);
  return {text:truncated?text.slice(0,half)+marker+text.slice(-(limit-marker.length-half)):text,characters:text.length,truncated,selection:truncated?'head_and_tail':'full'};
}
type Average={n:number;mean:number};
type Delta={name:string;defined:number;mean_delta:number|null;warmup:number;missing:number;prior_count_min:number|null;prior_count_max:number|null;raw_mean_projection:number|null;raw_defined:number};
const update=(b:Average,v:number)=>{b.n++;b.mean+=(v-b.mean)/b.n;};
function deltas(names:string[]):Delta[]{return names.map(name=>({name,defined:0,mean_delta:null,warmup:0,missing:0,prior_count_min:null,prior_count_max:null,raw_mean_projection:null,raw_defined:0}));}
function rawMean(values:Delta[],name:string,rows:number,assetMatches:boolean){
  const d=assetMatches?values.find(d=>d.name===name):undefined;
  return {status:!assetMatches?'steering_asset_not_captured':!d?'direction_not_captured':!rows?'channel_absent':!d.raw_defined?'missing':'defined',
    raw_mean_projection:d?.raw_mean_projection??null,defined:d?.raw_defined??0,missing:d?.missing??rows,rows};
}
function finish(rows:number,values:Delta[]){
  const moves=values.filter(v=>v.defined>0).sort((a,b)=>Math.abs(b.mean_delta!)-Math.abs(a.mean_delta!)||a.name.localeCompare(b.name)).slice(0,4);
  return {status:rows===0?'channel_absent':moves.length?'defined':'no_warmed_values',rows,moves,
    undefined:values.filter(v=>!v.defined).map(({name,warmup,missing})=>({name,warmup,missing})),omitted_defined_directions:values.filter(v=>v.defined>0).length-moves.length};
}
/** Descriptive reductions only, over the saved projections/top-k. No new model/lens inference. */
export function summariseTurn(entries:readonly EntryRecord[],request:string,rules:AlertRules,labels:Record<string,string|null>,tokenizer:string|null){
  validRules(rules);const target=resolveTurn(entries,request),turn=target.turn,emotion=turn.header?.settings?.emotion;
  const unique=new Map<string,ReadoutTurnRecord>();for(const e of entries)if(e.kind==='glimmer.readout.turn'&&e.id<=target.turnEntry){const t=e.data as ReadoutTurnRecord;unique.set(t.request,t);}
  const history=[...unique.values()].filter(t=>t.turn<=turn.turn).sort((a,b)=>a.turn-b.turn);
  if(history.reduce((n,t)=>n+t.records.length,0)>250_000)throw Error('This conversation exceeds the annotation summary limit.');
  const means=new Map<string,Average>();
  const layers=(emotion?.layers??[]).map(layer=>({layer,reasoning:deltas(emotion!.names),answer:deltas(emotion!.names)}));
  const count={reasoning:0,answer:0};
  for(const t of history){
    const settings=t.header?.settings?.emotion;
    for(const row of [...new Map(t.records.map(r=>[r.pos,r])).values()].sort((a,b)=>a.pos-b.pos)){
      if(row.source!=='generated'||!['reasoning','answer'].includes(row.channel??''))continue;
      const channel=row.channel as 'reasoning'|'answer',current=t.request===request;if(current)count[channel]++;
      if(!settings)continue;
      for(const layer of layers)for(const [i,name]of emotion!.names.entries()){
        const key=`${settings.directions}:${layer.layer}:${name}`,b=means.get(key)??{n:0,mean:0},v=projection(t,row,layer.layer,name);
        if(current){const d=layer[channel][i];
          if(v!==null){d.raw_defined++;d.raw_mean_projection=(d.raw_mean_projection??0)+(v-(d.raw_mean_projection??0))/d.raw_defined;}
          if(v===null)d.missing++;
          else if(b.n<32)d.warmup++;
          else {d.defined++;const delta=v-b.mean;d.mean_delta=(d.mean_delta??0)+(delta-(d.mean_delta??0))/d.defined;
            d.prior_count_min=Math.min(d.prior_count_min??b.n,b.n);d.prior_count_max=Math.max(d.prior_count_max??b.n,b.n);}
        }
        // Exactly the alerts' running reasoning-only mean; answers never update it.
        if(channel==='reasoning'&&v!==null){update(b,v);means.set(key,b);}
      }
    }
  }
  const workspace=turn.header?.settings?.workspace,li=workspace?.layers.indexOf(44)??-1;
  const frequency=(channel:'reasoning'|'answer')=>{
    let rows=0,defined=0,unknownLabels=0;const counts=new Map<number,number>();
    for(const r of turn.records){if(r.source!=='generated'||r.channel!==channel)continue;rows++;
      const top=li<0?null:r.workspace?.top[li];if(!top)continue;defined++;
      for(const id of new Set(top.map(x=>x[0]))){counts.set(id,(counts.get(id)??0)+1);if(typeof labels[id]!=='string')unknownLabels++;}
    }
    return {status:li<0?'layer_not_captured':!rows?'channel_absent':!defined?'missing':'defined',rows,defined_rows:defined,missing_rows:rows-defined,unknown_label_occurrences:unknownLabels,
      tokens:[...counts].sort((a,b)=>b[1]-a[1]||a[0]-b[0]).slice(0,8).map(([id,count])=>({id,label:labels[id]??null,count}))};
  };
  const watchRules={...rules,emotion:{...rules.emotion,enabled:false}},alerts=evaluateAlerts({turns:[turn]},watchRules,labels);
  return {schema:'glimmer.turn-signals/1',scope:'Descriptive internal-signal summary, not feelings, knowledge, a causal explanation or a verdict.',
    text:{prompt:excerpt(target.prompt,4000),reasoning:excerpt(target.reasoning,6000),answer:excerpt(target.answer,12000),stop_reason:target.stopReason},
    steering:!turn.header?{status:'unknown'}:turn.header.steer?{status:'recorded',...turn.header.steer}:{status:'none'},
    emotion:{status:emotion?'captured':'not_captured',directions:emotion?.directions??null,units:'raw signed direction projection',
      statistic:'Channel mean of (row projection minus the mean of all earlier defined generated reasoning rows in this conversation, same asset/name/layer). Current row enters the mean after its test; answer rows never enter it. Includes earlier rows of this turn, including steered rows: these differences do not estimate the steering effect or compare with an unsteered run. Largest absolute mean differences shown, not significance.',
      raw_statistic:'raw_mean_projection is the channel mean of defined raw projections, not a difference. It includes warm-up rows. raw_defined (or defined in steered_direction) counts its contributing rows; missing rows are omitted, never zero-filled.',
      warmup_required:32,layers:layers.map(l=>({layer:l.layer,reasoning:finish(count.reasoning,l.reasoning),answer:finish(count.answer,l.answer),
        steered_direction:turn.header?.steer?{name:turn.header.steer.direction,reasoning:rawMean(l.reasoning,turn.header.steer.direction,count.reasoning,turn.header.steer.directions===emotion?.directions),answer:rawMean(l.answer,turn.header.steer.direction,count.answer,turn.header.steer.directions===emotion?.directions)}:null}))},
    workspace:{status:workspace?'captured':'not_captured',lens:workspace?.lens??null,top_k:workspace?.k??null,frequency_layer:44,tokenizer_sha256:tokenizer,
      interpretation:'Counts are rows containing a token ID in the supplied top-k, not word counts, probabilities or thoughts. Isolated-token labels can be incomplete.',
      reasoning:frequency('reasoning'),answer:frequency('answer'),watch:{status:!rules.workspace.enabled?'disabled':!workspace?.layers.includes(rules.workspace.layer)?'layer_not_captured':tokenizer===null?'labels_unavailable':'observed',
        layer:rules.workspace.layer,prefixes:[...rules.workspace.prefixes],unknown_rows:alerts.unknownWorkspaceRows,
        rule:'Generated reasoning only; an entry requires an adjacent known absence. Gaps/unknowns are not absences. First entry per prefix/turn, with re-entries.',
        groups:alerts.groups.filter(g=>g.first.match).map(g=>({prefix:g.first.match!.prefix,word:g.first.match!.label,id:g.first.match!.token,first_position:g.first.pos,rank:g.first.match!.rank,reentries:g.entries-1,last_position:g.lastPos}))}}
  };
}
export type SignalSummary=ReturnType<typeof summariseTurn>;
