/** Browser-safe, inert presentation of one historical experiment. No executable profile. */
export type RecordedCommand = {returncode:number; stdout:string; stderr:string; seconds:number; reason:string|null};
export type RecordedGrade = {
  passed:boolean; status:string; oracle:string; normalization:string;
  compile:RecordedCommand; run:RecordedCommand|null; reason:string|null;
  codeSha256:string; taskSha256:string;
};
export type RecordedEvent = {
  id:string; name:string; args:Record<string,unknown>; output:string; isError:boolean;
  durationMs:number; startLine:number; endLine:number; assistant:string; reasoning:string;
};
export type RecordedFile = {path:string; sha256:string; text:string};
export type RecordedArm = {
  label:'Base'|'Improved'; harnessSha256:string; directory:string; launchSha256:string;
  traceSha256:string; completionSha256:string; gradeSha256:string;
  sessionId:string; timestamp:string; prompt:string; events:RecordedEvent[]; finalAnswer:string;
  modelCalls:number; seconds:number; source:string; sourceSha256:string; grade:RecordedGrade;
  files:RecordedFile[];
};
export type RecordedTaskResult = {passed:boolean; rollout:'complete'|'failed'; modelCalls:number|null; reason:string|null};
export type RecordedComparison = {
  format:'glimmer.recorded-harness/1'; run:string; task:string; model:string;
  temperature:number; thinking:string; maxModelCalls:number;
  base:RecordedArm; improved:RecordedArm;
  tasks:{id:string; base:RecordedTaskResult; improved:RecordedTaskResult}[];
  results:{base:number; improved:number; total:number};
  changes:{path:string; kind:'added'|'removed'|'changed'|'unchanged'; base:RecordedFile|null; improved:RecordedFile|null}[];
  inputs:Record<string,string>;
};

/** Exact file-level change classification. Full before/after text is displayed, not a generated summary. */
export function harnessChanges(base:RecordedFile[],improved:RecordedFile[]):RecordedComparison['changes']{
  const a=new Map(base.map(f=>[f.path,f])),b=new Map(improved.map(f=>[f.path,f]));
  return [...new Set([...a.keys(),...b.keys()])].sort().map(path=>({path,
    kind:!a.has(path)?'added':!b.has(path)?'removed':a.get(path)!.sha256===b.get(path)!.sha256?'unchanged':'changed',
    base:a.get(path)??null,improved:b.get(path)??null}));
}
