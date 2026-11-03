import path from 'node:path';
import {homedir} from 'node:os';
import {isDeepStrictEqual as equal} from 'node:util';
import {pinnedBytes,sha256} from './pinned-recording.ts';
import {byteLevelLabels} from './pi-assets.ts';
import {validateWorkspaceTurn,type WorkspaceTurn} from '../workspace/model.ts';
import {setupNames,type Setup,type RecordingChoice,type Step2Info} from '../workspace/step2.ts';

// Metadata/pins only. Never bundle the recordings, tokenizer or generated output.
export const step2Config={
  root:path.join(homedir(),'nomos_data/sysprompt-study-20261101'),
  tokenizer:path.join(homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json'),
  tokenizerPin:'c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d',
  pins:{
    'step2/recordings-manifest.json':'625cca4c5e5be2c58474000255767c8b7a66b46abbfbc98714e43f4a0f81fbaf',
    'step2/task-response-start.json':'7b995cfe8c181da90c2872730cb6d16a4281fb49c95b17e2ca5e06a60cdf76c0',
    'step2/scenarios.json':'3c8c61b526e0d3891a3fbd9bdb70e06ddf6d84f711e8dc3968c406a74878d841',
    'items.json':'9e48225dcbaf3a91ed16f709bc25e85983777726792c87515f90da08c463b962',
    'conditions.json':'d65f4888582502fc1eeb4cd941bb7926eb77086a970f66e58fd7ee1ce078d2b3',
    'step2/capture-20261101T122747/identity.json':'970074dfa6436bb56f2be4e713bd73deda4e10a023f75b8a3a5e1045655a2abd',
    'step2/precheck-20261101T122407/gates.json':'e5f4227ba62908338f3135a62e9b0fe20d9bfd6414e2428f2214df5f171e9917',
    'step2/precheck-20261101T122407/identity.json':'f7aa16b5b87d37cca7da35bdd572dda16ce91f1663b4dda7fa9d24484069941a'
  }
};
type Config={root:string;tokenizer:string;tokenizerPin:string;pins:Record<string,string>};
type Raw=Record<string,any>;
const capture='capture-20261101T122747',precheck='step2/precheck-20261101T122407/';
function requireValue(ok:unknown,message:string):asserts ok {if(!ok)throw Error(message);}
const validId=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{12}$/.test(v);
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);

/** Injectable only for offline fixtures. The HTTP adapter uses the fixed instance
 * below; no request parameter can select a root, pin, path or alternate source.
 */
export function createStep2Source(cfg:Config){
  async function json(file:string){return JSON.parse((await pinnedBytes(cfg.root,file,cfg.pins[file],2_000_000)).toString());}
  async function metadata(){
    const [manifest,markers,scenarios,items,conditions,identity]=await Promise.all([
      'step2/recordings-manifest.json','step2/task-response-start.json','step2/scenarios.json','items.json','conditions.json',`step2/${capture}/identity.json`
    ].map(json));
    requireValue(manifest.capture===capture&&manifest.identity_sha256===cfg.pins[`step2/${capture}/identity.json`],'Capture binding mismatch.');
    requireValue(Array.isArray(manifest.recordings)&&manifest.recordings.length===48&&Array.isArray(scenarios)&&scenarios.length===48
      &&Array.isArray(markers.turns)&&markers.turns.length===48&&Array.isArray(items),'Invalid recording inventory.');
    requireValue(markers.meta?.selected_post_hoc===true&&markers.meta.not_a_decision_time===true
      &&markers.meta.registered_window_unchanged==='first 64 answer tokens'&&typeof markers.meta.rule==='string'&&typeof markers.meta.selected_by==='string','Invalid marker scope.');
    requireValue(identity.identity?.tokenizer_sha256===cfg.tokenizerPin&&hash(identity.identity?.directions)&&hash(identity.identity?.lens)
      &&equal(identity.settings?.workspace,{layers:[36,40,44,48],k:16})&&typeof identity.identity.schedule==='string'
      &&identity.sampling?.samples===1&&identity.sampling.first_sample===0&&identity.sampling.temperature===1
      &&identity.sampling.top_p===0.95&&identity.sampling.top_k===64&&typeof identity.provenance?.started==='string','Invalid capture identity.');
    requireValue(equal(Object.keys(conditions).sort(),Object.keys(setupNames).sort()),'Invalid setup roster.');
    const byKey=(xs:Raw[],field:string)=>{const m=new Map(xs.map(x=>[x[field],x]));requireValue(m.size===xs.length,'Duplicate metadata key.');return m;};
    const scenarioMap=byKey(scenarios,'key'),markerMap=byKey(markers.turns,'key'),itemMap=byKey(items,'id');
    const seen=new Set<string>(),selectedItems=new Set<string>();
    const entries=manifest.recordings.map((entry:Raw)=>{
      const {key,item,setup}=entry,scenario=scenarioMap.get(key),marker=markerMap.get(key),task=itemMap.get(item),condition=conditions[setup];
      requireValue(validId(item)&&Object.hasOwn(setupNames,setup)&&key===`${setup}-${item}`&&!seen.has(key),'Invalid recording key.');
      seen.add(key);selectedItems.add(item);
      requireValue(entry.path===`${capture}/${key}/runs/base.s0.free.json`&&hash(entry.sha256),'Invalid recording path.');
      requireValue(task&&condition&&scenario&&marker&&entry.entity===task.entity&&entry.stratum===task.stratum
        &&typeof task.user==='string'&&typeof condition.system==='string'&&(condition.opener===null||typeof condition.opener==='string'),'Metadata join mismatch.');
      const user=condition.opener===null?task.user:condition.opener+'\n\n'+task.user;
      requireValue(equal(scenario.modes,['free'])&&scenario.onset===null&&scenario.reference===null&&Array.isArray(scenario.prompt_ids)
        &&scenario.prompt_ids.every((id:unknown)=>Number.isSafeInteger(id)&&Number(id)>=0)
        &&scenario.source?.item===item&&scenario.source.setup===setup&&scenario.source.entity===entry.entity
        &&scenario.source.system_sha256===sha256(condition.system)&&scenario.source.user_sha256===sha256(user),'Prompt binding mismatch.');
      requireValue(marker.setup===setup&&marker.entity===entry.entity&&Number.isSafeInteger(marker.char_offset)&&marker.char_offset>=0
        &&Number.isSafeInteger(marker.answer_token_index)&&marker.answer_token_index>=0&&Number.isSafeInteger(marker.answer_tokens)
        &&marker.answer_token_index<marker.answer_tokens&&typeof marker.anchor==='string'&&marker.anchor.length>0
        &&Array.isArray(marker.skipped_paragraphs)&&marker.skipped_paragraphs.every((s:unknown)=>typeof s==='string'),'Invalid task-response marker.');
      requireValue(typeof entry.registered_label==='string'&&typeof entry.corrected_label==='string','Missing classifier labels.');
      return {entry,scenario,marker,task,condition,user};
    });
    requireValue(selectedItems.size===8&&[...selectedItems].every(item=>Object.keys(setupNames).every(setup=>seen.has(`${setup}-${item}`))),'Incomplete item/setup grid.');
    return {entries,identity,markers};
  }
  async function catalog():Promise<RecordingChoice[]>{
    const {entries}=await metadata();
    return entries.map(({entry}:{entry:Raw})=>({id:`step2-${entry.key}`,title:`${entry.entity} · ${setupNames[entry.setup as Setup]}`,collection:'step2',item:entry.item,entity:entry.entity,setup:entry.setup}));
  }
  async function recording(id:string):Promise<WorkspaceTurn>{
    // Refuse arbitrary input before touching the metadata or any path.
    requireValue(/^step2-(default|fact_checker|confident|calibrated|kind|cold)-[a-f0-9]{12}$/.test(id),'Unknown recording.');
    const {entries,identity,markers}=await metadata(),chosen=entries.find(({entry}:{entry:Raw})=>id===`step2-${entry.key}`);
    requireValue(chosen,'Unknown recording.');
    const {entry,scenario,marker,task,condition,user}=chosen;
    const d=JSON.parse((await pinnedBytes(cfg.root,`step2/${entry.path}`,entry.sha256,16_000_000)).toString());
    requireValue(d.schema==='nomos.steerdiff.run/1'&&d.run_id===`${entry.key}.base.s0`&&d.mode==='free'&&d.reference===null
      &&d.arm?.name==='base'&&d.arm.steer===null&&d.arm.sample===0&&Number.isSafeInteger(d.arm.seed)
      &&equal(d.identity,identity.identity)&&equal(d.settings,identity.settings)&&equal(d.prompt_ids,scenario.prompt_ids)
      &&d.summary?.header?.steer===null&&equal(d.summary.header.settings,d.settings),'Recording metadata mismatch.');
    const vocab=byteLevelLabels(JSON.parse((await pinnedBytes(path.dirname(cfg.tokenizer),path.basename(cfg.tokenizer),cfg.tokenizerPin,32_000_000)).toString()));
    const labels:WorkspaceTurn['labels']={};
    for(const r of d.records){labels[r.token]=vocab.get(r.token)??null;for(const top of r.workspace?.top??[])for(const [token] of top)labels[token]=vocab.get(token)??null;}
    const step2:Step2Info={key:entry.key,item:entry.item,entity:entry.entity,setup:entry.setup,system:condition.system,opener:condition.opener,user,
      sample:0,seed:d.arm.seed,capturedAt:identity.provenance.started,
      sampling:{temperature:identity.sampling.temperature,top_p:identity.sampling.top_p,top_k:identity.sampling.top_k},
      assets:{directions:identity.identity.directions,lens:identity.identity.lens},
      classifier:{registered:entry.registered_label,sensitivity:entry.corrected_label,
        provenance:'Prime’s pinned Step 2 manifest. Registered refusal-opener classifier; post-hoc C1+C2 from relabel_check.py. Sensitivity labels are not truth judgements.',
        warning:entry.key==='fact_checker-e7e6accc4bcd'?'Lens exception: C2 started at character 764, skipping the actual opening refusal and labelling the reply “answered”. This sensitivity label is not evidence that the opening answered the question.':null},
      marker:{rule:markers.meta.rule,selectedBy:markers.meta.selected_by,postHoc:true,notDecisionTime:true,charOffset:marker.char_offset,
        answerTokenIndex:marker.answer_token_index,anchor:marker.anchor,skipped:marker.skipped_paragraphs,answerTokens:marker.answer_tokens},pins:{...cfg.pins}};
    return validateWorkspaceTurn({id,title:`${task.entity} · ${setupNames[entry.setup as Setup]}`,prompt:task.user,source:'recorded',
      layers:d.settings.workspace.layers,k:d.settings.workspace.k,records:d.records,labels,steering:'No steering',step2,
      provenance:{label:'System-prompt study · Step 2 · 1 Nov 2026 · sample 0',sha256:entry.sha256,tokenizer:cfg.tokenizerPin,schedule:d.identity.schedule}});
  }
  async function precheckEvidence(){
    const [gates,identity]=await Promise.all([json(precheck+'gates.json'),json(precheck+'identity.json')]);
    return {scope:'Separate precheck: one default scenario, not 48 repeat checks. Main-run G3/G7 are not repeat tests.',
      pins:{gates:cfg.pins[precheck+'gates.json'],identity:cfg.pins[precheck+'identity.json']},
      gates,schedule:identity.identity?.schedule,tokenizer:identity.identity?.tokenizer_sha256};
  }
  return {catalog,recording,precheckEvidence};
}
export const step2Source=createStep2Source(step2Config);
