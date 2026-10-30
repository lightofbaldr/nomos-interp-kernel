import type {EntryRecord} from 'glimmer-agent/pi';
export const ANNOTATION_KIND='glimmer.meta.annotation';
export type RejectionDiagnostic={rule:string;word_count:number|null;character_count:number|null;finish_reason:string|null;phrase_id:number|null};
export type Annotation={v:1;request:string;turn:number;model:string;status:'pending'|'complete'|'failed';started_at:string;finished_at:string|null;
  payload_json:string;payload_sha256:string;note:string|null;error:string|null;diagnostic?:RejectionDiagnostic|null};
export function annotationFor(entries:readonly EntryRecord[],request:string):Annotation|undefined{
  return [...entries].sort((a,b)=>b.id-a.id).find(e=>e.kind===ANNOTATION_KIND&&(e.data as Annotation)?.request===request)?.data as Annotation|undefined;
}
