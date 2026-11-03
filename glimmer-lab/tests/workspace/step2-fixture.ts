import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {sha256} from '../../src/lib/server/pinned-recording.ts';
import {createStep2Source} from '../../src/lib/server/workspace-step2.ts';
import {setupNames} from '../../src/lib/workspace/step2.ts';

export async function fixture(){
  const root=await mkdtemp(path.join(tmpdir(),'glimmer-step2-'));
  const capture='capture-20261101T122747',pins:Record<string,string>={};
  const save=async(file:string,obj:unknown)=>{const bytes=JSON.stringify(obj);await mkdir(path.dirname(path.join(root,file)),{recursive:true});await writeFile(path.join(root,file),bytes);return sha256(bytes);};
  const put=async(file:string,obj:unknown)=>pins[file]=await save(file,obj);
  const tokenizerPin=await save('tokenizer.json',{model:{type:'BPE',vocab:Object.fromEntries(Array.from({length:16},(_,i)=>[i===0?'!':`Ġword${String.fromCharCode(97+i)}`,32+i]))},decoder:{type:'ByteLevel'},added_tokens:[]});
  const cfg={root,tokenizer:root+'/tokenizer.json',tokenizerPin,pins};
  const items=Array.from({length:8},(_,i)=>({id:i===4?'e7e6accc4bcd':String(i).padStart(12,'0'),entity:`Entity ${i}`,user:`Question ${i}?`,stratum:i<4?'fake':'obscure_real',gt_real:i>=4}));
  const conditions=Object.fromEntries(Object.keys(setupNames).map(setup=>[setup,{system:`System ${setup}`,opener:['kind','cold'].includes(setup)?`Opener ${setup}`:null}]));
  const identity={identity:{directions:'d'.repeat(64),lens:'e'.repeat(64),tokenizer_sha256:tokenizerPin,schedule:'fixture schedule'},settings:{workspace:{layers:[36,40,44,48],k:16}},sampling:{samples:1,first_sample:0,temperature:1,top_p:0.95,top_k:64},provenance:{started:'2026-11-01T00:00:00Z'}};
  const recordings:any[]=[],scenarios:any[]=[],turns:any[]=[];
  for(const item of items)for(const setup of Object.keys(setupNames)){
    const key=`${setup}-${item.id}`,condition=conditions[setup];
    const answerTokens=setup==='calibrated'?15:70;
    const records=[{pos:0,token:32,source:'prompt',channel:null,workspace:null,emotion:null,missing:['prompt']},
      ...Array.from({length:answerTokens},(_,i)=>({pos:i+1,token:33,source:'generated',channel:'answer',emotion:null,missing:[],workspace:{top:Array.from({length:4},()=>Array.from({length:16},(_,j)=>[j+32,16-j]))}}))];
    const user=condition.opener===null?item.user:condition.opener+'\n\n'+item.user;
    scenarios.push({key,prompt_ids:[32],onset:null,reference:null,modes:['free'],source:{item:item.id,entity:item.entity,setup,system_sha256:sha256(condition.system),user_sha256:sha256(user)}});
    turns.push({key,setup,entity:item.entity,char_offset:setup==='kind'?100:0,answer_token_index:setup==='kind'?65:setup==='cold'?3:0,anchor:'fixture anchor',skipped_paragraphs:setup==='kind'?['greeting']:[],answer_tokens:answerTokens});
    const file=`${capture}/${key}/runs/base.s0.free.json`;
    const doc={schema:'nomos.steerdiff.run/1',run_id:`${key}.base.s0`,mode:'free',reference:null,prompt_ids:[32],arm:{name:'base',steer:null,sample:0,seed:1},identity:identity.identity,settings:identity.settings,records,summary:{header:{steer:null,settings:identity.settings}}};
    recordings.push({key,path:file,sha256:await save('step2/'+file,doc),setup,item:item.id,entity:item.entity,stratum:item.stratum,registered_label:'hedged',corrected_label:'answered'});
  }
  await put('items.json',items);await put('conditions.json',conditions);await put('step2/scenarios.json',scenarios);
  await put('step2/'+capture+'/identity.json',identity);
  await put('step2/recordings-manifest.json',{capture,identity_sha256:pins['step2/'+capture+'/identity.json'],recordings});
  await put('step2/task-response-start.json',{meta:{selected_post_hoc:true,not_a_decision_time:true,registered_window_unchanged:'first 64 answer tokens',rule:'fixture rule',selected_by:'fixture'},turns});
  await put('step2/precheck-20261101T122407/gates.json',{default:{G1_repeat:{pass:true}}});
  await put('step2/precheck-20261101T122407/identity.json',identity);
  return {root,cfg,save,put,source:createStep2Source(cfg),close:()=>rm(root,{recursive:true,force:true})};
}
