// Synthetic presentation fixture. Never exported into the app's real data catalog.
import type { Arm, Bundle, BundleIndex, ComparisonRef, Mode, Position, Scenario } from '../../src/lib/steering-diff/model.ts';
export function scenarioFixture() {
  const settings={emotion:{layers:[20,24,29,36,44,51],names:['joyful','hopeful','compassionate','calm','desperate','afraid','sad','anxious']},workspace:{layers:[36,40,44,48],k:16}};
  const source={path:'/synthetic/episode.json',sha256:'a'.repeat(64),question:'SYNTHETIC FIXTURE — not a model result',label:'lie',classifier:'overclaim',prompt_len:94,answer_start:2209,thinking_tail:256};
  const replay:Scenario={key:'TEST-RECORDED',title:'SYNTHETIC recorded-sequence replay',onset:93,modes:['matched'],max_new:null,P:1953,reference_len:1004,source};
  const answer:Scenario={...replay,key:'TEST-CONTINUATION',title:'SYNTHETIC answer continuation',modes:['free'],max_new:1024,P:2209,reference_len:null};
  const arms:Arm[]=[{name:'base',kind:null,s:null},{name:'joy_plus',kind:'joyful',s:.5},{name:'joy_minus',kind:'joyful',s:-.5},{name:'sad_minus',kind:'sad',s:-.5},{name:'random_minus',kind:'random1',s:-.5}];
  const refs:ComparisonRef[]=[replay,answer].flatMap(s=>arms.slice(1).map(a=>({prompt:s.key,arm:a.name,mode:s.modes[0],path:`${s.key}/${a.name}.${s.modes[0]}.json`,divergence:s.modes[0]==='free'?{index:3,position:s.P+3,baseline_token:1,steered_token:2}:null})));
  const index:BundleIndex={schema:'nomos.steerdiff.ui/1',run:'SYNTHETIC-scenario-fixture',identity:{steer_layer:24,schedule:'Synthetic UI fixture, no engine'},settings,prompts:{[replay.key]:replay.title,[answer.key]:answer.title},scenarios:{[replay.key]:replay,[answer.key]:answer},arms,display_k:8,gates:{},comparisons:refs};
  function makeBundle(ref:ComparisonRef):Bundle {
    const sc=index.scenarios![ref.prompt], arm=arms.find(a=>a.name===ref.arm)!;
    const num=sc.P+(ref.mode==='matched'?1004:3);
    const positions:Position[]=Array.from({length:num},(_,pos)=>({pos,token:1,source:pos<sc.P?'prompt':'generated',channel:pos<sc.P?null:pos<(ref.mode==='matched'?2207:sc.P+2)?'reasoning':pos<2209?'template':'answer',missing:[],emotion_delta:settings.emotion.layers.map(()=>settings.emotion.names.map(()=>0)),workspace:pos<sc.P?null:settings.workspace.layers.map(layer=>({layer,missing:[],candidates:[{id:1,baseline:2,steered:3,baseline_rank:1,steered_rank:1,baseline_below:null,steered_below:null,delta:1,delta_min:null,delta_max:null,status:'both'}]}))}));
    // The free answer has no newly stepped reasoning: its thinking was already prefilled.
    if(ref.mode==='free')for(const p of positions)if(p.source==='generated')p.channel=p.pos<sc.P+2?'template':'answer';
    const base={run_id:ref.prompt+'.base',arm:{name:'base',steer:null},identity:{fixture:true},tokens:Array(ref.mode==='matched'?1004:24).fill(1)};
    return {schema:'nomos.steerdiff.ui/1',prompt:ref.prompt,scenario:structuredClone(sc),arm:{...arm},mode:ref.mode,vocab:{'1':' test','2':' alternative'},text:{baseline:{reasoning:ref.mode==='matched'?'SYNTHETIC stepped thinking tail.':'',answer:'SYNTHETIC reference answer.'},steered:{reasoning:'',answer:ref.mode==='matched'?'SYNTHETIC reference answer.':'SYNTHETIC newly generated answer.'}},comparison:{schema:'nomos.steerdiff/1',mode:ref.mode,reference:ref.mode==='matched'?source.path:null,P:sc.P,display_k:8,settings,divergence:ref.divergence,tracks:ref.mode==='matched'?null:{baseline:Array(21).fill(1),steered:Array(21).fill(2)},positions,baseline:base,steered:{...base,run_id:ref.prompt+'.'+arm.name,arm:{name:arm.name,steer:{layer:24,kind:arm.kind!,s:arm.s!,from_pos:93}},tokens:ref.mode==='matched'?base.tokens:[1,1,1,...Array(21).fill(2)]}}};
  }
  return {index,makeBundle};
}
