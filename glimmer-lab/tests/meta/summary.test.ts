import test from 'node:test';
import assert from 'node:assert/strict';
import {summariseTurn,resolveTurn} from '../../src/lib/server/meta-summary.ts';
import {defaultRules,evaluateAlerts} from '../../src/lib/pi/alerts.ts';

export function fixture(){
  const row=(pos:number,value:number,channel='reasoning',top=501)=>({pos,token:9,source:'generated',channel,emotion:{proj:[[value,0]],norm:[1]},workspace:{top:[[[top,1]]]},missing:[]});
  const turn:any={request:'req-1',task:10,turn:0,status:'complete',recorded:true,error:null,header:{settings:{emotion:{directions:'d',layers:[29],names:['afraid','calm']},workspace:{lens:'j',layers:[44],k:1}},steer:{directions:'d',direction:'afraid',layer:24,strength:-.5,from_pos:5}},summary:{},records:[...Array.from({length:32},(_,i)=>row(i,1)),row(32,5,'reasoning',502),row(33,2,'reasoning'),row(34,7,'reasoning',502),row(35,8,'answer',502)]};
  const entries:any[]=[{id:1,kind:'pi.input',model:[{role:'user',content:'Question'}]},{id:2,kind:'glimmer.readout.turn',data:turn},{id:3,kind:'pi.message',byTaskId:10,model:[{role:'assistant',stopReason:'stop',content:[{type:'thinking',thinking:'Reasoning'},{type:'text',text:'Answer'}]}]}];
  return {turn,entries,labels:{501:' fact',502:' cannot'},rules:defaultRules()};
}
test('turn text is joined by durable task, not the current settings or latest answer',()=>{
  const f=fixture(),t=resolveTurn(f.entries,'req-1');assert.equal(t.prompt,'Question');assert.equal(t.answer,'Answer');assert.equal(t.responseEntry,3);
  f.entries.push({id:4,kind:'other',byTaskId:999,model:[{role:'assistant',content:'Other answer'}]});assert.equal(resolveTurn(f.entries,'req-1').answer,'Answer');
  assert.throws(()=>resolveTurn(f.entries,'absent'));
  f.turn.status='aborted';assert.throws(()=>resolveTurn(f.entries,'req-1'));
});
test('same prior reasoning baseline, warm-up and actual signed steering, with no raw arrays',()=>{
  const f=fixture(),s=summariseTurn(f.entries,'req-1',f.rules,f.labels,'pin');
  assert.deepEqual(s.steering,{status:'recorded',...f.turn.header.steer});
  const e=s.emotion.layers[0];assert.equal(e.layer,29);assert.equal(e.reasoning.rows,35);assert.equal(e.reasoning.moves[0].name,'afraid');
  const d1=5-1,d2=2-(37/33),d3=7-(39/34);assert.ok(Math.abs(e.reasoning.moves[0].mean_delta!-(d1+d2+d3)/3)<1e-12);
  assert.equal(e.reasoning.moves[0].defined,3);assert.equal(e.reasoning.moves[0].warmup,32);
  assert.ok(Math.abs(e.answer.moves[0].mean_delta!-(8-46/35))<1e-12);assert.equal(e.answer.moves[0].prior_count_min,35);
  assert.equal(JSON.stringify(s).includes('"proj"'),false);assert.equal(JSON.stringify(s).includes('"norm"'),false);
});
test('watch groups agree with existing alert code and workspace counts name tokens, not consumed text',()=>{
  const f=fixture(),s=summariseTurn(f.entries,'req-1',f.rules,f.labels,'pin');
  const groups=evaluateAlerts({turns:[f.turn]},f.rules,f.labels).groups;
  assert.equal(s.workspace.watch.groups.length,groups.length);assert.equal(s.workspace.watch.groups[0].word,' cannot');
  assert.equal(s.workspace.watch.groups[0].first_position,32);assert.equal(s.workspace.watch.groups[0].reentries,1);
  assert.deepEqual(s.workspace.reasoning.tokens.map(x=>[x.id,x.count]),[[501,33],[502,2]]);
  assert.equal(s.workspace.answer.tokens[0].count,1);
});
test('absent channels, missing labels, zero and short warm-up remain distinct',()=>{
  const f=fixture();f.turn.records=f.turn.records.slice(0,2);f.turn.records[0].emotion=null;
  const s=summariseTurn(f.entries,'req-1',f.rules,{},null),e=s.emotion.layers[0];
  assert.equal(e.reasoning.moves.length,0);assert.equal(e.reasoning.status,'no_warmed_values');assert.equal(e.answer.status,'channel_absent');
  assert.equal(s.workspace.watch.status,'labels_unavailable');assert.equal(s.workspace.reasoning.tokens[0].label,null);
  f.turn.header.settings.workspace=null;f.turn.header.steer=null;
  const off=summariseTurn(f.entries,'req-1',f.rules,{},null);assert.equal(off.workspace.status,'not_captured');assert.equal(off.steering.status,'none');
});
test('only earlier turns contribute; truncation is explicit',()=>{
  const f=fixture();f.entries[2].model[0].content[0].thinking='x'.repeat(15000);f.entries[2].model[0].stopReason='length';
  const future=structuredClone(f.turn);future.request='future';future.turn=1;future.records.forEach((r:any)=>r.emotion.proj[0][0]=999);
  f.entries.push({id:4,kind:'glimmer.readout.turn',data:future});
  const s=summariseTurn(f.entries,'req-1',f.rules,f.labels,'pin');assert.equal(s.text.reasoning.truncated,true);assert.ok(s.text.reasoning.text.length<=6000);assert.equal(s.text.stop_reason,'length');assert.equal(s.emotion.layers[0].reasoning.moves[0].warmup,32);
});
test('the prior mean spans earlier turns but never mixes direction assets or layer axes',()=>{
  for(const mode of ['same','different_asset','different_layer']){
    const f=fixture(),prior=structuredClone(f.turn);prior.request='prior';prior.turn=0;
    prior.records=prior.records.filter((r:any)=>r.channel==='reasoning');prior.records.forEach((r:any)=>r.emotion.proj[0][0]=100);
    if(mode==='different_asset')prior.header.settings.emotion.directions='different';
    if(mode==='different_layer')prior.header.settings.emotion.layers=[20];
    f.turn.turn=1;f.entries.forEach(e=>e.id+=10);f.entries.unshift({id:2,kind:'glimmer.readout.turn',data:prior});
    const result=summariseTurn(f.entries,'req-1',f.rules,f.labels,'pin').emotion.layers[0].reasoning.moves.find(m=>m.name==='afraid')!;
    assert.equal(result.warmup,mode==='same'?0:32);assert.equal(result.prior_count_min,mode==='same'?35:32);
    if(mode==='same')assert.ok(result.mean_delta!<0);else assert.ok(result.mean_delta!>0);
  }
});
test('recorded readout absence remains unknown, not a fabricated zero-valued signal',()=>{
  const f=fixture();f.turn.header=null;f.turn.records=[];
  const result=summariseTurn(f.entries,'req-1',f.rules,{},null);
  assert.deepEqual(result.steering,{status:'unknown'});assert.equal(result.emotion.status,'not_captured');assert.deepEqual(result.emotion.layers,[]);
  assert.equal(result.workspace.status,'not_captured');assert.equal(result.workspace.top_k,null);assert.deepEqual(result.workspace.answer.tokens,[]);
});
test('raw means expose a constant push that cancels in the within-turn prior-mean differences',()=>{
  const base=fixture(),pushed=fixture();pushed.turn.records.forEach((r:any)=>r.emotion.proj[0][0]+=60);
  const a=summariseTurn(base.entries,'req-1',base.rules,base.labels,'pin'),b=summariseTurn(pushed.entries,'req-1',pushed.rules,pushed.labels,'pin');
  for(const channel of ['reasoning','answer'] as const){
    const x=a.emotion.layers[0][channel].moves.find(d=>d.name==='afraid')!,y=b.emotion.layers[0][channel].moves.find(d=>d.name==='afraid')!;
    assert.ok(Math.abs(x.mean_delta!-y.mean_delta!)<1e-12);assert.ok(Math.abs(y.raw_mean_projection!-x.raw_mean_projection!-60)<1e-12);
    assert.equal(b.emotion.layers[0].steered_direction![channel].raw_mean_projection,y.raw_mean_projection);
    assert.equal(b.emotion.layers[0].steered_direction![channel].defined,channel==='reasoning'?35:1);
  }
  assert.match(b.emotion.statistic,/including steered rows/);assert.match(b.emotion.raw_statistic,/not a difference/);
});
test('the steered raw direction is retained outside the four largest moves; missing and foreign assets stay explicit',()=>{
  const f=fixture();f.turn.header.settings.emotion.names=['a','b','c','d','e','afraid'];f.turn.records.forEach((r:any)=>r.emotion.proj[0]=[1,2,3,4,5].map(x=>x*r.emotion.proj[0][0]).concat(60));
  const s=summariseTurn(f.entries,'req-1',f.rules,f.labels,'pin'),l=s.emotion.layers[0];assert.equal(l.reasoning.moves.some(d=>d.name==='afraid'),false);assert.equal(l.steered_direction?.reasoning.raw_mean_projection,60);
  f.turn.records.forEach((r:any)=>r.emotion=null);const missing=summariseTurn(f.entries,'req-1',f.rules,{},null).emotion.layers[0].steered_direction!;
  assert.equal(missing.reasoning.raw_mean_projection,null);assert.equal(missing.reasoning.missing,35);assert.equal(missing.reasoning.status,'missing');
  f.turn.header.steer.directions='foreign';const foreign=summariseTurn(f.entries,'req-1',f.rules,{},null).emotion.layers[0].steered_direction!;
  assert.equal(foreign.reasoning.status,'steering_asset_not_captured');assert.equal(foreign.reasoning.raw_mean_projection,null);
});
