export const MAX_TRACE_BYTES=2_000_000;
export type Concept={label:string; scores:number[]};
export type Emotion={label:string; score:number};
export type Frame={sequence:number; tokenIndex:number; elapsedMs:number; textOffset:number; concepts:Concept[]; emotions:Emotion[]};
export type Trace={
  schema:'nomos.inspection.v1'; source:'demo'|'recorded'; runId:string; modelId:string; title:string; capturedAt:string;
  probe:{id:string; method:string; version:string; scoreKind:'cosine'; calibration:string|null; layerIds:number[]; kernelSha256:string|null};
  messages:[{role:'user';text:string},{role:'assistant';text:string}]; frames:Frame[];
};

function fail(message:string):never{throw new Error(`Trace rejected: ${message}`);}
function record(value:unknown,keys:string[],name:string):Record<string,unknown>{
  if(!value||typeof value!=='object'||Array.isArray(value))fail(`${name} must be an object`);
  const result=value as Record<string,unknown>;
  if(Object.keys(result).some(k=>!keys.includes(k)))fail(`${name} contains unsupported fields`);
  return result;
}
function string(value:unknown,name:string,max=120):string{
  if(typeof value!=='string'||!value.trim()||value.length>max)fail(`${name} must be nonempty text of at most ${max} characters`);
  return value;
}
function number(value:unknown,name:string,min:number,max:number,integer=false):number{
  if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))fail(`${name} is outside its declared range`);
  return value;
}
function array(value:unknown,name:string,min=1,max=32):unknown[]{
  if(!Array.isArray(value)||value.length<min||value.length>max)fail(`${name} must contain ${min}-${max} entries`);
  return value;
}
function unique(values:(string|number)[],name:string){if(new Set(values).size!==values.length)fail(`${name} contains duplicate identities`);}

/** Validate once at the boundary. Values are probe scores, never probabilities
 * or a safety grade. Metadata is self-reported, not loaded-kernel attestation. */
export function parseTrace(input:unknown):Trace{
  const root=record(input,['schema','source','runId','modelId','title','capturedAt','probe','messages','frames'],'trace');
  if(root.schema!=='nomos.inspection.v1')fail('unsupported schema');
  if(root.source!=='demo'&&root.source!=='recorded')fail('a file cannot claim to be a live connection');
  const capturedAt=string(root.capturedAt,'capturedAt',40);
  if(!/^\d{4}-\d{2}-\d{2}T/.test(capturedAt)||!Number.isFinite(Date.parse(capturedAt)))fail('capturedAt is not an ISO timestamp');
  const p=record(root.probe,['id','method','version','scoreKind','calibration','layerIds','kernelSha256'],'probe');
  if(p.scoreKind!=='cosine')fail('only explicitly signed cosine scores are supported');
  const layers=array(p.layerIds,'layerIds',1,64).map((v,i)=>number(v,`layerIds[${i}]`,0,1023,true));unique(layers,'layerIds');
  if(layers.some((v,i)=>i>0&&v<=layers[i-1]))fail('layerIds must be ordered');
  const hash=p.kernelSha256===null?null:string(p.kernelSha256,'kernelSha256',64);
  if(hash!==null&&!/^[0-9a-f]{64}$/.test(hash))fail('kernelSha256 must be a SHA-256 or explicit null');
  const messages=array(root.messages,'messages',2,2).map((v,i)=>{
    const m=record(v,['role','text'],`messages[${i}]`);
    if(m.role!==(i===0?'user':'assistant'))fail('one ordered user/assistant pair is required');
    return {role:m.role,text:string(m.text,`messages[${i}].text`,50_000)};
  }) as Trace['messages'];
  const frames=array(root.frames,'frames',1,1000).map((v,i):Frame=>{
    const f=record(v,['sequence','tokenIndex','elapsedMs','textOffset','concepts','emotions'],`frames[${i}]`);
    const concepts=array(f.concepts,'concepts',0,24).map((value,j)=>{
      const c=record(value,['label','scores'],`concepts[${j}]`);
      const scores=array(c.scores,'scores',layers.length,layers.length).map(v=>number(v,'concept score',-1,1));
      return {label:string(c.label,'concept label',60),scores};
    });unique(concepts.map(c=>c.label),'concepts');
    const emotions=array(f.emotions,'emotions',0,16).map((value,j)=>{
      const e=record(value,['label','score'],`emotions[${j}]`);
      return {label:string(e.label,'emotion label',60),score:number(e.score,'emotion score',-1,1)};
    });unique(emotions.map(e=>e.label),'emotions');
    return {sequence:number(f.sequence,'sequence',0,1_000_000,true),tokenIndex:number(f.tokenIndex,'tokenIndex',0,1_000_000,true),elapsedMs:number(f.elapsedMs,'elapsedMs',0,86_400_000),textOffset:number(f.textOffset,'textOffset',0,messages[1].text.length,true),concepts,emotions};
  });
  frames.forEach((f,i)=>{if(i){const previous=frames[i-1];if(f.sequence<=previous.sequence||f.tokenIndex<previous.tokenIndex||f.elapsedMs<previous.elapsedMs||f.textOffset<previous.textOffset)fail('frame sequence, positions, time and text offsets must be monotonic');}});
  return {schema:'nomos.inspection.v1',source:root.source,runId:string(root.runId,'runId'),modelId:string(root.modelId,'modelId'),title:string(root.title,'title',100),capturedAt,
    probe:{id:string(p.id,'probe id'),method:string(p.method,'probe method'),version:string(p.version,'probe version'),scoreKind:'cosine',calibration:p.calibration===null?null:string(p.calibration,'calibration',200),layerIds:layers,kernelSha256:hash},messages,frames};
}
export function parseTraceText(text:string):Trace{
  if(new TextEncoder().encode(text).byteLength>MAX_TRACE_BYTES)fail('file exceeds 2 MB');
  let parsed:unknown;try{parsed=JSON.parse(text);}catch{fail('file is not valid JSON');}
  return parseTrace(parsed);
}
export function scoreLabel(score:number){return `${score>=0?'+':''}${score.toFixed(2)}`;}
export function answerAt(trace:Trace,index:number){const frame=trace.frames[index];if(!frame)fail('selected frame is absent');return trace.messages[1].text.slice(0,frame.textOffset);}
