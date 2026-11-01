import test from 'node:test';
import assert from 'node:assert/strict';
import {turnCounts,directionMeans,directionSeries,steeringText,trajectory,workspaceCoverage} from '../../src/lib/inspection/model.ts';
import type {TurnView} from '../../src/lib/pi/model.ts';
import {emotionDirections,SP1_DIRECTIONS} from '../../src/lib/pi/assets.ts';
import {payloadReading} from '../../src/lib/inspection/payload.ts';
function fixture():TurnView{
  return {request:'one',turn:0,task:1,status:'complete',error:null,summary:null,
    header:{v:'nomos.readout/1',conversation:'1',turn:0,response_id:'one',settings:{emotion:{directions:'d',names:['afraid','calm'],layers:[29]},workspace:null,prompt:'new'},steer:{directions:'d',direction:'afraid',layer:24,strength:.5,from_pos:0}},
    records:[{pos:0,token:0,source:'prompt',channel:null,emotion:{proj:[[100,0]],norm:[1]},workspace:null,missing:[]},
      ...[0,2,null,4].map((v,i)=>({pos:i+1,token:1,source:'generated' as const,channel:'reasoning' as const,emotion:v===null?null:{proj:[[v,0]],norm:[1]},workspace:null,missing:[]})),
      {pos:5,token:1,source:'generated',channel:'answer',emotion:{proj:[[-60,0]],norm:[1]},workspace:null,missing:[]}]};
}
test('counts separate prompt, reasoning, answer, reused and absent workspace',()=>{
  const t=fixture();assert.deepEqual(turnCounts(t),{rows:6,prompt:1,reasoning:4,answer:1,other:0,emotion:5,workspace:0,reused:null});
  t.summary={header:t.header!,ranges:{reused:[0,30],prompt:[30,31],generated:[31,36]},counts:{records:6,emotion_valid:5,workspace_valid:0},missing:{}};
  assert.equal(turnCounts(t).reused,30);t.records=[];assert.equal(turnCounts(t).reasoning,0);
});
test('raw channel means include real zero, exclude missing and never mix prompt or answer',()=>{
  const t=fixture();assert.deepEqual(directionMeans(t,29,'reasoning')[0],{name:'afraid',mean:2,defined:3,missing:1,total:4});
  assert.equal(directionMeans(t,29,'answer')[0].mean,-60);
  assert.equal(directionMeans(t,29,'reasoning')[1].mean,0);
  assert.equal(directionMeans(t,20,'reasoning')[0].mean,null);
  t.records=[];assert.equal(directionMeans(t,29,'answer')[0].mean,null);
});
test('layer and name axes come from this turn, never a current asset selector',()=>{
  const t=fixture();t.header!.settings!.emotion.names.reverse();assert.equal(directionSeries(t,29,'afraid','answer')[0].value,0);
  t.records[1].emotion!.proj[0][1]=NaN;assert.equal(directionSeries(t,29,'afraid','reasoning')[0].value,null);
});
test('trajectory breaks at undefined and missing positions and spans real positions',()=>{
  const t=trajectory([{pos:100,value:0},{pos:101,value:4},{pos:102,value:null},{pos:103,value:3},{pos:108,value:2}]);
  assert.equal(t.paths.length,3);assert.equal(t.first,100);assert.equal(t.last,108);assert.equal(t.defined,4);assert.equal(t.total,5);assert.equal(t.extent,4);
  assert.equal(trajectory([]).defined,0);assert.equal(trajectory([{pos:2,value:null}]).paths.length,0);
});
test('recorded steering is distinct from absent header and explicit off',()=>{
  const t=fixture();assert.equal(steeringText(t),'afraid +0.5 · L24 · from position 0');t.header!.steer=null;assert.equal(steeringText(t),'Off');t.header=null;assert.equal(steeringText(t),'Unknown');
});
test('watch-list coverage does not treat absent capture or unknown labels as absence',()=>{
  const t=fixture();assert.deepEqual(workspaceCoverage(t,44,{}),{defined:0,unknown:4,total:4});
  t.header!.settings!.workspace={lens:'l',layers:[44],k:1};t.records[1].workspace={top:[[[42,1]]]};
  assert.equal(workspaceCoverage(t,44,{}).unknown,4);assert.equal(workspaceCoverage(t,44,{42:' word'}).defined,1);
});
test('emotion defaults never follow registry order or choose random-only assets',()=>{
  const random={id:'r',names:['random1','random2'],layers:[29]},sp1={id:SP1_DIRECTIONS,names:['desperate','afraid','angry'],layers:[20,24,29]};
  const a={supported:true,error:null,directions:[random,sp1],lenses:[],tokenizer:null};
  assert.equal(emotionDirections(a),sp1);a.directions.reverse();assert.equal(emotionDirections(a),sp1);
  a.directions=[random];assert.equal(emotionDirections(a),undefined);
  a.directions=[{...sp1,id:'named'}];assert.equal(emotionDirections(a)?.id,'named');a.directions.push({...sp1,id:'other'});assert.equal(emotionDirections(a),undefined);
});
test('payload view reads saved raw and difference fields separately, absent is not zero',()=>{
  const summary={schema:'glimmer.turn-signals/1',steering:{status:'recorded',direction:'afraid',strength:.5,layer:24},emotion:{layers:[{layer:29,reasoning:{rows:40,status:'defined',moves:[{name:'afraid',raw_mean_projection:60,mean_delta:4,raw_defined:40,defined:8,missing:0}]},answer:{rows:1,status:'no_warmed_values',moves:[]},steered_direction:{name:'afraid',answer:{raw_mean_projection:0,defined:1,missing:0}}}]}};
  const annotation:any={payload_json:JSON.stringify({messages:[{role:'user',content:JSON.stringify(summary)}]})};
  const r=payloadReading(annotation)!;assert.equal(r.layers[0].channels[0].rows[0].raw,60);assert.equal(r.layers[0].channels[0].rows[0].delta,4);assert.equal(r.layers[0].channels[0].rows[0].differenceDefined,8);
  assert.equal(r.layers[0].channels[1].rows[0].raw,0);assert.equal(r.layers[0].channels[1].rows[0].delta,null);assert.equal(r.prompt.text,null);
  annotation.payload_json='invalid';assert.equal(payloadReading(annotation),null);
});
