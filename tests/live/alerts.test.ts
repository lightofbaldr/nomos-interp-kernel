import {test} from 'node:test';
import assert from 'node:assert/strict';
import {defaultRules,evaluateAlerts,validRules,type AlertRules} from '../../src/lib/pi/alerts.ts';
import {emptyPi,reducePi} from '../../src/lib/pi/model.ts';
const rules=():AlertRules=>({...defaultRules(),emotion:{enabled:true,name:'afraid',layer:29,threshold:5,side:'above'}});
function turn(values:number[],offset=0):any{return {request:'one',turn:0,status:'complete',header:{settings:{emotion:{directions:'d',names:['afraid'],layers:[29]},workspace:{layers:[44],k:2,lens:'l'}}},records:values.map((v,i)=>({pos:i+offset,token:10,source:'generated',channel:'reasoning',emotion:{proj:[[v]],norm:[100]},workspace:{top:[[[1,5],[2,4]]]},missing:[]})),error:null,summary:null};}
const labels={1:' word',2:' token',3:' cannot',4:' inventing'};
test('prior-only mean, warmup, crossings, signed direction and no repeated badges while high',()=>{
  const t=turn([...Array(34).fill(1),12,12,0,12]);const result=evaluateAlerts({turns:[t]},rules(),labels);
  assert.deepEqual(result.events.map(x=>x.pos),[34,37]);assert.match(result.events[0].detail,/prior mean 1\.0000 from 34/);
  assert.equal(evaluateAlerts({turns:[turn([...Array(32).fill(1),12])]},rules(),labels).total,0,'no fabricated crossing at the first eligible row');
  const down=rules();down.emotion.side='below';assert.equal(evaluateAlerts({turns:[turn([...Array(34).fill(12),0])]},down,labels).total,1);
});
test('missing, non-reasoning, absent layer and position gaps never act as zeros or establish crossings',()=>{
  for(const kind of ['missing','answer','gap']){const t=turn([...Array(34).fill(1),12]);if(kind==='missing')t.records[33].emotion=null;if(kind==='answer')t.records[33].channel='answer';if(kind==='gap')t.records[34].pos+=1;assert.equal(evaluateAlerts({turns:[t]},rules(),labels).total,0,kind);}
  const r=rules();r.emotion.layer=24;assert.equal(evaluateAlerts({turns:[turn(Array(40).fill(12))]},r,labels).baselineSamples,0);
});
test('workspace prefix entry is reasoning-only, relative to a known preceding set, and carries actual token/rank',()=>{
  const t=turn([1,1,1,1]);t.records[1].workspace.top[0][1]=[3,4];t.records[2].workspace.top[0][1]=[3,4];t.records[3].workspace.top[0][1]=[4,4];
  const got=evaluateAlerts({turns:[t]},defaultRules(),labels);assert.deepEqual(got.events.map(x=>x.pos),[1,3]);assert.match(got.events[0].detail,/#3.*rank 2/);assert.match(got.events[1].rule,/invent/);
  t.records[1].channel='answer';assert.equal(evaluateAlerts({turns:[t]},defaultRules(),labels).events.some(e=>e.pos===1),false);
});
test('unknown workspace/labels, first row and gaps cannot produce watch-list entrances',()=>{
  for(const how of ['label','workspace','gap','first']){const t=turn([1,1]);t.records[1].workspace.top[0][1]=[3,4];
    if(how==='label')t.records[0].workspace.top[0][0]=[999,9];if(how==='workspace')t.records[0].workspace=null;if(how==='gap')t.records[1].pos+=1;if(how==='first')t.records.shift();
    assert.equal(evaluateAlerts({turns:[t]},defaultRules(),labels).total,0,how);
  }
});
test('terminal/snapshot replacement and duplicated records do not multiply alerts',()=>{
  const t=turn([...Array(34).fill(1),12]);let state=emptyPi();state=reducePi(state,{type:'readout.turn',turn:t,entryId:1} as any);
  const before=evaluateAlerts(state,rules(),labels);state=reducePi(state,{type:'readout.turn',turn:t,entryId:1} as any);assert.deepEqual(evaluateAlerts(state,rules(),labels),before);
  const dup=structuredClone(t);dup.records.push(dup.records.at(-1));assert.deepEqual(evaluateAlerts({turns:[dup,dup]},rules(),labels),before);
});
test('baselines continue across turns only for the same asset, never across conversations',()=>{
  const one=turn(Array(34).fill(1)),two=turn([1,12]);two.request='two';two.turn=1;
  assert.equal(evaluateAlerts({turns:[one,two]},rules(),labels).total,1);
  two.header.settings.emotion.directions='another';assert.equal(evaluateAlerts({turns:[one,two]},rules(),labels).total,0);
  assert.equal(evaluateAlerts({turns:[two]},rules(),labels).total,0);
});
test('rules reject malformed thresholds and watch lists',()=>{
  for(const threshold of [0,-1,NaN,Infinity]){const r=rules();r.emotion.threshold=threshold;assert.throws(()=>validRules(r));}
  const r=rules();r.workspace.prefixes=[' '];assert.throws(()=>validRules(r));
});
test('flickering watch words group at their first entry per turn, with exact re-entry counts and matched tokens',()=>{
  const t=turn(Array(9).fill(1));
  for(const i of [1,3,5,7])t.records[i].workspace.top[0]=[[3,5],[4,4]];
  const got=evaluateAlerts({turns:[t]},defaultRules(),labels);
  assert.equal(got.total,8);assert.equal(got.events.length,8);
  assert.equal(got.groupTotal,2);assert.equal(got.groupCounts.one,2);
  assert.deepEqual(got.groups.map(g=>[g.first.pos,g.entries,g.lastPos,g.first.match?.label,g.first.match?.token]),[[1,4,7,' cannot',3],[1,4,7,' inventing',4]]);
  assert.equal(got.groups[0].first.token,10,'consumed position token remains distinct from the matching workspace candidate');
  const next=structuredClone(t);next.request='two';next.turn=1;
  assert.equal(evaluateAlerts({turns:[t,next]},defaultRules(),labels).groupTotal,4);
  assert.deepEqual(evaluateAlerts({turns:[t,t]},defaultRules(),labels),got,'duplicate terminal/snapshot input does not inflate entries');
});
test('grouping never counts an entry across unknown data and retains the first entry beyond the detailed trail limit',()=>{
  const t=turn(Array(450).fill(1));for(let i=1;i<450;i+=2)t.records[i].workspace.top[0][1]=[3,4];
  t.records[10].workspace=null;
  const got=evaluateAlerts({turns:[t]},defaultRules(),labels);
  assert.equal(got.total,224);assert.equal(got.events.length,200);
  assert.equal(got.groups.length,1);assert.equal(got.groups[0].first.pos,1);assert.equal(got.groups[0].entries,224);assert.equal(got.groups[0].lastPos,449);
});
test('a leading-space prefix requires a word-start token; a prefix is still not a semantic word classifier',()=>{
  for(const [label,want]of [['INVENT',false],['inventory',false],[' INVENT',true],[' inventory',true]] as const){
    const t=turn([1,1]);t.records[1].workspace.top[0][1]=[4,4];
    assert.equal(evaluateAlerts({turns:[t]},defaultRules(),{...labels,4:label}).total,want?1:0,label);
  }
  const r=defaultRules();r.workspace.prefixes=['invent'];
  const t=turn([1,1]);t.records[1].workspace.top[0][1]=[4,4];
  assert.equal(evaluateAlerts({turns:[t]},r,{...labels,4:'INVENT'}).total,1,'no leading space explicitly allows a standalone token prefix');
});
