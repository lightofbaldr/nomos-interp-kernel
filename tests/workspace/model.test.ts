import {test} from 'node:test';
import assert from 'node:assert/strict';
import {candidates,filterReason,fromLive,promptForTurn,selectedPosition,validateWorkspaceTurn,type WorkspaceTurn} from '../../src/lib/workspace/model.ts';
import {emptyPi,type TurnView} from '../../src/lib/pi/model.ts';
export function fixture():WorkspaceTurn {
  return {id:'example',title:'Fixture',source:'recorded',prompt:'Fixture prompt',layers:[36,40,44,48],k:4,steering:'Steering off',provenance:{label:'Synthetic unit fixture'},labels:{1:' paper',2:' cannot',3:'ing',4:'<|eot|>'},
    records:[{pos:5,token:1,source:'prompt',channel:null,emotion:null,workspace:null,missing:['workspace_prompt_v1']},
      {pos:7,token:1,source:'generated',channel:'answer',emotion:null,workspace:{top:[36,40,44,48].map(()=>[[4,8],[3,6],[2,-1],[1,-2]])},missing:[]}]};
}
test('word filter is reversible and retains raw ranks, scores and unknown labels',()=>{
  const t=fixture(),before=JSON.stringify(t),all=candidates(t,t.records[1],44)!;
  assert.deepEqual(all.filter(c=>!c.hiddenReason).map(c=>[c.id,c.rank,c.score]),[[2,3,-1],[1,4,-2]]);
  assert.equal(all.length,4);assert.equal(JSON.stringify(t),before);
  delete t.labels['3'];assert.equal(candidates(t,t.records[1],44)![1].hiddenReason,null);
  for(const s of ['<|end|>','\n','ing','.', '123', '\uFFFD'])assert.ok(filterReason(s));
  for(const s of [' cannot',' blood',' don’t'])assert.equal(filterReason(s),null);
});
test('missing layer and prompt data never become zeros or an empty measured list',()=>{
  const t=fixture();assert.equal(candidates(t,t.records[0],44),null);assert.equal(candidates(t,t.records[1],51),null);
  assert.equal(selectedPosition(t,null,'answer')?.pos,7);assert.equal(selectedPosition(t,5,'all')?.pos,5);
  assert.equal(selectedPosition(t,5,'reasoning'),undefined);
});
test('recorded schema refuses altered shapes, duplicate positions, IDs and non-finite scores',()=>{
  assert.equal(validateWorkspaceTurn(fixture()).records.length,2);
  const mutations:((t:any)=>unknown)[]=[t=>t.records[1].pos=5,t=>t.layers.push(48),t=>t.records[1].workspace.top.pop(),t=>t.records[1].workspace.top[0][0][1]=NaN,t=>t.records[1].workspace.top[0][1][0]=4,t=>t.records[1].workspace.top[0][1][1]=99];
  for(const mutate of mutations){
    const t=fixture();mutate(t as any);assert.throws(()=>validateWorkspaceTurn(t));
  }
});
test('a selected turn uses its own prior prompt, not the first or the latest prompt',()=>{
  const pi=emptyPi();pi.entries=[
    {id:1,kind:'pi.input',model:[{role:'user',content:'First question'}]},
    {id:2,kind:'glimmer.readout.turn',data:{request:'first'}},
    {id:3,kind:'pi.input',model:[{role:'user',content:'Second question'}]},
    {id:4,kind:'glimmer.readout.batch',data:{request:'second'}},
    {id:5,kind:'glimmer.annotation',data:{text:'Not a prompt'}},
    {id:6,kind:'pi.input',model:[{role:'user',content:'Future question'}]},
  ] as any;
  const turn=(request:string)=>({request} as TurnView);
  assert.equal(promptForTurn(pi,turn('first')),'First question');
  assert.equal(promptForTurn(pi,turn('second')),'Second question');
  assert.equal(promptForTurn(pi,turn('unpersisted')),'');
});
test('a missing live header is unknown, never steering off',()=>{
  const t={request:'a',turn:0,status:'streaming',header:null,records:[]} as unknown as TurnView;
  const rendered=fromLive(t,{},'');assert.equal(rendered.steering,'Steering not recorded');assert.deepEqual(rendered.layers,[]);
});
