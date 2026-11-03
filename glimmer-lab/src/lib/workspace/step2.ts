import type {ReadoutRecord} from 'glimmer-agent/contract';

export const setupNames={default:'Default',fact_checker:'Fact checker',confident:'Confident',calibrated:'Calibrated',kind:'Kind opener',cold:'Cold opener'} as const;
// A catalog entry, not a fallback recording. The server must still verify it.
export const firstStep2Recording='step2-default-e39fe23efc76';
export type Setup=keyof typeof setupNames;
export type RecordingChoice={id:string;title:string;collection:'step2';item:string;entity:string;setup:Setup};
export type Step2Info={
  key:string;item:string;entity:string;setup:Setup;system:string;opener:string|null;user:string;
  sample:0;seed:number;capturedAt:string;sampling:{temperature:number;top_p:number;top_k:number};
  assets:{directions:string;lens:string};
  classifier:{registered:string;sensitivity:string;provenance:string;warning:string|null};
  marker:{rule:string;selectedBy:string;postHoc:true;notDecisionTime:true;charOffset:number;answerTokenIndex:number;anchor:string;skipped:string[];answerTokens:number};
  pins:Record<string,string>;
};

/** Channel-labelled consumed positions only. Neither jump infers a decision time. */
export function answerWindows(records:ReadoutRecord[],info:Step2Info){
  const answer=records.filter(r=>r.source==='generated'&&r.channel==='answer');
  return {registered:answer.slice(0,64).map(r=>r.pos),taskStart:answer[info.marker.answerTokenIndex]?.pos??null,answerCount:answer.length};
}
