import {pinnedBytes,sha256} from './pinned-recording.ts';
import {recordedHarnessConfig} from './recorded-harness-config.ts';
import {harnessChanges,isImproverScaffold,type RecordedArm,type RecordedCommand,type RecordedComparison,type RecordedEvent,type RecordedFile,type RecordedGrade,type RecordedTaskResult} from '../workbench/recorded.ts';

type Config={root:string;task:string;base:{id:string;directory:string;sha256:string};improved:{id:string;directory:string;sha256:string};pins:Record<string,string>};
type Obj=Record<string,any>;
function requireThat(ok:unknown,why:string):asserts ok{if(!ok)throw Error(`Invalid recorded harness: ${why}`);}
function obj(x:unknown):Obj{requireThat(x&&typeof x==='object'&&!Array.isArray(x),'object');return x as Obj;}
function arr(x:unknown):any[]{requireThat(Array.isArray(x),'array');return x;}
function str(x:unknown):string{requireThat(typeof x==='string','string');return x;}
function integer(x:unknown):number{requireThat(Number.isSafeInteger(x)&&Number(x)>=0,'nonnegative integer');return x as number;}
function number(x:unknown):number{requireThat(typeof x==='number'&&Number.isFinite(x)&&x>=0,'finite nonnegative number');return x;}
function bool(x:unknown):boolean{requireThat(typeof x==='boolean','boolean');return x;}
function nullable(x:unknown):string|null{return x===null?null:str(x);}
function pin(x:unknown):string{requireThat(typeof x==='string'&&/^[a-f0-9]{64}$/.test(x),'sha256');return x;}
function canonical(x:any):string{return JSON.stringify(x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,JSON.parse(canonical(x[k]))])):Array.isArray(x)?x.map(v=>JSON.parse(canonical(v))):x);}
function equal(a:unknown,b:unknown,why:string){requireThat(canonical(a)===canonical(b),why);}
function textContent(content:unknown,kind='text'):string{
  return arr(content).filter(p=>obj(p).type===kind).map(p=>str(p[kind])).join('\n');
}
const ALWAYS=new Set(['systemprompt.md','guardrails.md','LongTermMEMORY.md','tool_descriptions/tool_guidance.md']);
function surface(file:string){return ALWAYS.has(file)||/^skills\/[a-z0-9_-]+\/SKILL\.md$/.test(file)||/^skills\/[a-z0-9_-]+\/scripts\/[A-Za-z0-9_.-]+$/.test(file)||/^extensions\/[a-z0-9_-]+\.ts$/.test(file)||/^scripts\/[a-z0-9_]+\.py$/.test(file)||/^sub_agents\/[a-z0-9_-]+\/AGENT\.md$/.test(file);}
/** Python json.dumps(tree, sort_keys=True), the recorded executor's harness identity.
 * Its read_tree normalizes text newlines and excludes four README stubs. */
export function harnessDigest(files:RecordedFile[]):string{
  const quote=(s:string)=>JSON.stringify(s).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const entries=files.filter(f=>!isImproverScaffold(f.path)&&surface(f.path)).sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
  return sha256('{'+entries.map(f=>`${quote(f.path)}: ${quote(f.text.replace(/\r\n?/g,'\n'))}`).join(', ')+'}');
}
function command(x:unknown):RecordedCommand{
  const c=obj(x);requireThat(Number.isSafeInteger(c.returncode),'exit status');
  return {returncode:c.returncode,stdout:str(c.stdout),stderr:str(c.stderr),seconds:number(c.seconds),reason:nullable(c.reason)};
}
function grade(x:unknown,taskSha:string,codeSha:string):RecordedGrade{
  const s=obj(x),g=obj(s.ground_truth),c=command(g.compile),r=g.run===null?null:command(g.run);
  requireThat(g.task_sha256===taskSha&&g.code_sha256===codeSha,'grader source binding');
  requireThat(g.status==='scored'&&g.oracle==='compile+stdout'&&g.kind==='function','grade type');
  requireThat(obj(g.toolchain_health).pass===true,'toolchain health');
  const passed=bool(g.passed);requireThat(s.all_pass===passed&&s.n_passed===(passed?1:0)&&s.n_criteria===1,'grade verdict');
  requireThat(s.quality_not_in_ground_truth===true,'separate quality score');
  if(passed)requireThat(c.returncode===0&&r?.returncode===0,'passing exit statuses');
  if(c.returncode!==0)requireThat(r===null&&!passed,'no run after compile failure');
  return {passed,status:g.status,oracle:g.oracle,normalization:str(g.stdout_normalization),compile:c,run:r,
    reason:nullable(g.reason),taskSha256:pin(g.task_sha256),codeSha256:pin(g.code_sha256)};
}
function taskResult(x:unknown):RecordedTaskResult{
  const row=obj(x),s=obj(row.score),g=obj(s.ground_truth);
  requireThat(row.rollout==='complete'||row.rollout==='failed','rollout status');
  if(row.rollout==='failed'){
    requireThat(g.status==='rollout_failed'&&g.passed===false&&s.all_pass!==true,'failed rollout verdict');
    return {passed:false,rollout:'failed',modelCalls:integer(row.model_calls),reason:nullable(row.reason)};
  }
  const checked=grade(s,pin(g.task_sha256),pin(g.code_sha256));
  return {passed:checked.passed,rollout:'complete',modelCalls:null,reason:checked.reason};
}
/** Full saved exchanges only. Stream deltas are not played as if newly generated. */
export function parseRecordedTrace(raw:string,prompt:string):{events:RecordedEvent[];sessionId:string;timestamp:string;finalAnswer:string;modelCalls:number}{
  const lines=raw.trimEnd().split('\n');requireThat(lines.length<=20000,'trace line cap');
  const rows=lines.map(line=>obj(JSON.parse(line)));
  requireThat(rows[0].type==='session'&&rows.at(-1)?.type==='agent_settled'&&rows.at(-1)?.aborted===false,'settled trace');
  const starts=new Map<string,{row:Obj;line:number}>(),calls=new Map<string,{row:Obj;assistant:string;reasoning:string}>();
  const ended=new Map<string,RecordedEvent>(),toolMessages=new Set<string>();const events:RecordedEvent[]=[];
  let modelCalls=0,userCount=0,finalAnswer='';
  for(const [i,row] of rows.entries()){
    if(row.type==='message_end'){
      const m=obj(row.message);
      if(m.role==='user'){equal(textContent(m.content),prompt,'trace prompt');userCount++;}
      if(m.role==='assistant'){
        modelCalls++;const assistant=textContent(m.content),reasoning=textContent(m.content,'thinking');
        const cs=arr(m.content).filter(c=>obj(c).type==='toolCall');
        for(const c of cs){const id=str(c.id);requireThat(!calls.has(id),'duplicate model call id');calls.set(id,{row:c,assistant,reasoning});}
        if(!cs.length)finalAnswer=assistant;
      }
      if(m.role==='toolResult'){
        const id=str(m.toolCallId),e=ended.get(id);requireThat(e&&!toolMessages.has(id),'tool message without unique completed exchange');
        equal([m.toolName,m.isError,textContent(m.content)],[e.name,e.isError,e.output],'tool result binding');toolMessages.add(id);
      }
    }
    if(row.type==='tool_execution_start'){
      const id=str(row.toolCallId),call=calls.get(id);requireThat(call&&!starts.has(id),'start must follow unique model call');
      equal([row.toolName,row.args],[call.row.name,call.row.arguments],'tool call arguments');
      starts.set(id,{row,line:i+1});
    }
    if(row.type==='tool_execution_end'){
      const id=str(row.toolCallId),start=starts.get(id),call=calls.get(id);
      requireThat(start&&call&&!ended.has(id)&&row.toolName===start.row.toolName,'end must follow unique matching start');
      const e={id,name:str(row.toolName),args:obj(start.row.args),output:textContent(obj(row.result).content),isError:bool(row.isError),durationMs:number(row.durationMs),startLine:start.line,endLine:i+1,assistant:call.assistant,reasoning:call.reasoning};
      ended.set(id,e);events.push(e);
    }
  }
  requireThat(userCount===1&&events.length>0&&finalAnswer.trim(),'complete selected task');
  requireThat(calls.size===starts.size&&calls.size===ended.size&&calls.size===toolMessages.size,'all exchanges complete');
  const timestamp=str(rows[0].timestamp);requireThat(Number.isFinite(Date.parse(timestamp)),'session time');
  return {events,sessionId:str(rows[0].id),timestamp,finalAnswer,modelCalls};
}

/** Fixed pins only. All consumed bytes are checked on every request. No directory
 * discovered from a URL, no execution/import of recorded scripts, no Pi runtime.
 * The roots must remain operator-owned and quiescent (same model as pinnedBytes).
 */
export async function loadRecordedHarness(cfg:Config=recordedHarnessConfig):Promise<RecordedComparison>{
  const bytes=new Map<string,Buffer>();let total=0;
  for(const [file,hash] of Object.entries(cfg.pins)){
    const cap=file.endsWith('/pi.jsonl')?4*1024*1024:1024*1024;
    const b=await pinnedBytes(cfg.root,file,hash,cap,file.startsWith('frozen/')||file.endsWith('/stderr.txt'));
    total+=b.length;requireThat(total<=16*1024*1024,'total input cap');bytes.set(file,b);
  }
  const get=(f:string)=>{const b=bytes.get(f);requireThat(b,'unlisted input');return b;};
  const json=(f:string)=>obj(JSON.parse(get(f).toString('utf8')));
  const split=json('split-run2.json'),roster=arr(split.test).map(str);
  requireThat(roster.length===10&&new Set(roster).size===10&&roster.includes(cfg.task),'ten-task roster');
  const arms:RecordedArm[]=[],evaluations:Obj[]=[];let identity:Obj|undefined,maxModelCalls=0;
  for(const [which,arm] of [cfg.base,cfg.improved].entries()){
    const evaluation=json(`mojo/evaluate-test-${arm.id}-driver.json`),corpus=obj(obj(evaluation.corpus).corpus);
    requireThat(evaluation.harness_sha256===arm.sha256&&evaluation.split==='test','aggregate harness');
    equal(obj(corpus.selected).test,roster,'aggregate task roster');equal(obj(corpus.selected).train,split.train,'training split');
    const rows=arr(evaluation.rows).map(obj);equal(rows.map(r=>r.task),roster,'complete ordered result strip');
    const taskSha=pin(obj(corpus.tasks)[cfg.task]),prefix=`mojo/executions/evaluate-test-${arm.id}/${cfg.task}/`;
    const launch=json(prefix+'launch.json'),complete=json(prefix+'complete.json'),result=json(prefix+'result.json'),ident=obj(launch.identity);
    equal(ident,complete.identity,'launch/completion identity');
    requireThat(ident.harness===arm.sha256&&ident.task===taskSha,'task/harness binding');
    const common={...ident};delete common.harness;
    if(identity)equal(common,identity,'same executor and task');else identity=common;
    requireThat(ident.temperature===0&&ident.model==='nomos-muse-glimmer-30b'&&ident.thinking==='high','recorded decoding');
    const argv=arr(launch.argv).map(str),prompt=str(argv.at(-1));requireThat(argv.at(-2)==='--','prompt argv');
    if(which)requireThat(prompt===arms[0].prompt,'paired prompt');
    const outputs=obj(complete.outputs);equal(Object.keys(outputs).sort(),['pi.jsonl','result.json','solution.mojo','stderr.txt'],'completion output set');
    for(const name of Object.keys(outputs))requireThat(outputs[name]===sha256(get(prefix+name)),'completion output binding');
    const code=get(prefix+'solution.mojo'),s=json(prefix+'mojo-grade.json');
    equal(s,rows.find(r=>r.task===cfg.task)?.score,'selected aggregate grade');
    const g=grade(s,taskSha,sha256(code));equal(obj(obj(s.ground_truth).toolchain_health).identity,ident.runtime,'grader toolchain');
    const trace=parseRecordedTrace(get(prefix+'pi.jsonl').toString('utf8'),prompt),usage=obj(result.usage);
    requireThat(result.delivered===true&&usage.settled===true&&usage.aborted===false,'delivered rollout');
    requireThat(result.model_calls===trace.modelCalls&&usage.turns===trace.modelCalls&&usage.tool_calls===trace.events.length,'trace counts');
    const limit=integer(launch.max_model_calls);requireThat(limit>=trace.modelCalls,'call limit');if(which)requireThat(limit===maxModelCalls,'same call limit');else maxModelCalls=limit;
    const files:RecordedFile[]=[...bytes].filter(([f])=>f.startsWith(arm.directory+'/')).map(([f,b])=>({path:f.slice(arm.directory.length+1),text:b.toString('utf8'),sha256:sha256(b)}));
    requireThat(files.length>0&&files.every(f=>isImproverScaffold(f.path)||surface(f.path)),'harness surface');
    requireThat(harnessDigest(files)===arm.sha256,'full harness identity');
    arms.push({label:which?'Improved':'Base',harnessSha256:arm.sha256,directory:arm.directory,launchSha256:cfg.pins[prefix+'launch.json'],traceSha256:cfg.pins[prefix+'pi.jsonl'],completionSha256:cfg.pins[prefix+'complete.json'],gradeSha256:cfg.pins[prefix+'mojo-grade.json'],...trace,prompt,seconds:number(result.seconds),source:code.toString('utf8'),sourceSha256:sha256(code),grade:g,files});
    evaluations.push(evaluation);
  }
  equal(evaluations[0].corpus,evaluations[1].corpus,'same corpus identity');
  const tasks=roster.map((id,i)=>({id,base:taskResult(evaluations[0].rows[i]),improved:taskResult(evaluations[1].rows[i])}));
  return {format:'glimmer.recorded-harness/1',run:'Run 2 · 2026-10-31',task:cfg.task,model:str(identity!.model),temperature:identity!.temperature,thinking:str(identity!.thinking),maxModelCalls,
    base:arms[0],improved:arms[1],tasks,results:{base:tasks.filter(t=>t.base.passed).length,improved:tasks.filter(t=>t.improved.passed).length,total:tasks.length},changes:harnessChanges(arms[0].files,arms[1].files),inputs:{...cfg.pins}};
}
