import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {harnessDigest,loadRecordedHarness} from '../../src/lib/server/recorded-harness.ts';
import {sha256} from '../../src/lib/server/pinned-recording.ts';

export async function fixture(){
  const root=await mkdtemp(path.join(tmpdir(),'recorded-harness-'));
  const cfg={root,task:'task-2',base:{id:'base',directory:'frozen/base',sha256:''},improved:{id:'improved',directory:'frozen/improved',sha256:''},pins:{} as Record<string,string>};
  const test=Array.from({length:10},(_,i)=>'task-'+i),taskSha=sha256('task'),prompt='Fixture request: write the requested definitions.';
  const put=async(file:string,value:any)=>{const text=typeof value==='string'?value:JSON.stringify(value);await mkdir(path.dirname(root+'/'+file),{recursive:true});await writeFile(root+'/'+file,text);cfg.pins[file]=sha256(text);return cfg.pins[file];};
  const read=async(file:string)=>JSON.parse(await readFile(root+'/'+file,'utf8'));
  const prefix=(arm='base')=>`mojo/executions/evaluate-test-${arm}/${cfg.task}/`;
  await put('split-run2.json',{train:['training-only'],test});await put('PREREGISTRATION-RUN2.md','Fixture protocol.');
  for(const [i,arm] of [cfg.base,cfg.improved].entries()){
    const texts:Record<string,string>={'systemprompt.md':'Fixture instructions '+i,'guardrails.md':'','LongTermMEMORY.md':'fixture','tool_descriptions/tool_guidance.md':'','skills/mojo-core/SKILL.md':'Fixture skill'};
    if(i)texts['skills/mojo-verify/SKILL.md']='Fixture verification skill <script>globalThis.BAD=true</script>';
    for(const [name,text] of Object.entries(texts))await put(arm.directory+'/'+name,text);
    arm.sha256=harnessDigest(Object.entries(texts).map(([file,text])=>({path:file,text,sha256:sha256(text)})));
    const identity={task:taskSha,harness:arm.sha256,model:'nomos-muse-glimmer-30b',temperature:0,thinking:'high',sandbox:'bwrap-model-only-v1',runtime:{fixture:true},runner_sha256:sha256('runner')};
    const pre=prefix(arm.id),source='Fixture submitted text '+i,args={path:'/work/solution.mojo',content:source},output='Wrote fixture source.',id='call1';
    const rows=[{type:'session',version:3,id:'session-'+arm.id,timestamp:'2026-10-31T21:19:23.489Z'},
      {type:'message_end',message:{role:'user',content:[{type:'text',text:prompt}]}},
      {type:'message_end',message:{role:'assistant',content:[{type:'thinking',thinking:'Fixture reasoning.'},{type:'toolCall',id,name:'write',arguments:args}]}},
      {type:'tool_execution_start',toolCallId:id,toolName:'write',args},
      {type:'tool_execution_end',toolCallId:id,toolName:'write',result:{content:[{type:'text',text:output}]},isError:false,durationMs:3},
      {type:'message_end',message:{role:'toolResult',toolCallId:id,toolName:'write',content:[{type:'text',text:output}],isError:false}},
      {type:'message_end',message:{role:'assistant',content:[{type:'text',text:'Fixture final answer.'}]}},
      {type:'agent_settled',aborted:false}];
    await put(pre+'launch.json',{identity,argv:['fixture','--',prompt],max_model_calls:32});
    await put(pre+'pi.jsonl',rows.map(x=>JSON.stringify(x)).join('\n')+'\n');
    await put(pre+'stderr.txt','');await put(pre+'solution.mojo',source);
    await put(pre+'result.json',{delivered:true,usage:{settled:true,aborted:false,tool_calls:1,turns:2},model_calls:2,seconds:5});
    await put(pre+'complete.json',{identity,outputs:Object.fromEntries(['pi.jsonl','stderr.txt','result.json','solution.mojo'].map(file=>[file,cfg.pins[pre+file]]))});
    const passed=!!i,cmd={returncode:i?0:1,stdout:'',stderr:i?'':'fixture compiler error',seconds:1,reason:null};
    const grade={n_passed:i,n_criteria:1,all_pass:passed,quality_not_in_ground_truth:true,muse_quality:null,ground_truth:{task_sha256:taskSha,code_sha256:sha256(source),kind:'function',oracle:'compile+stdout',stdout_normalization:'strip',compile:cmd,run:i?{...cmd,stdout:'ok\n'}:null,status:'scored',passed,toolchain_health:{pass:true,identity:identity.runtime},reason:null}};
    await put(pre+'mojo-grade.json',grade);
    await put(`mojo/evaluate-test-${arm.id}-driver.json`,{harness_sha256:arm.sha256,split:'test',rows:test.map(task=>({task,rollout:'complete',score:grade})),corpus:{corpus:{selected:{train:['training-only'],test},tasks:Object.fromEntries(test.map(t=>[t,taskSha]))}}});
  }
  const edit=async(file:string,fn:(v:any)=>void)=>{const x=await read(file);fn(x);await put(file,x);};
  const rebind=async(arm='base')=>edit(prefix(arm)+'complete.json',x=>{for(const name of Object.keys(x.outputs))x.outputs[name]=cfg.pins[prefix(arm)+name];});
  return {root,cfg,put,read,edit,prefix,rebind,load:()=>loadRecordedHarness(cfg),close:()=>rm(root,{recursive:true,force:true})};
}
