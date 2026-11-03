import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {TurnCheck} from 'glimmer-agent/contract';
import {reducePi,emptyPi,projection,selectedRow} from '../../src/lib/pi/model.ts';
const fixture=JSON.parse(readFileSync(new URL('./serve-5e4a100.json',import.meta.url),'utf8'));
test('recorded Python producer fixtures survive ordered batch/terminal/snapshot reduction without duplicates',()=>{
  for(const {name,block,chunks} of fixture.cases){
    const check=new TurnCheck(block);let state=emptyPi(),all:any[]=[];
    let header:any=null;
    for(const c of chunks){const u=check.take(c);if(!u)continue;header=u.header??header;all.push(...u.records);state=reducePi(state,{type:'readout.batch',entryId:1,batch:{request:name,turn:0,header,records:u.records}} as any);}
    const summary=check.close(chunks[0].id),turn={request:name,task:1,turn:0,status:'complete',recorded:true,header:summary.header,summary,records:all,error:null};
    state=reducePi(state,{type:'readout.turn',entryId:2,turn} as any);assert.equal(state.turns[0].records.length,summary.counts.records);
    state=reducePi(state,{type:'readout.turn',entryId:2,turn} as any);assert.equal(state.turns[0].records.length,all.length);
    const snapshot={pi:{type:'snapshot',entries:[{id:2,kind:'glimmer.readout.turn',data:turn}],generation:null,run:null,inbox:[]},settings:{version:0}};
    const restored=reducePi(emptyPi(),{type:'bridge.snapshot',snapshot} as any);assert.deepEqual(restored.turns,state.turns,name);
    const missing=all.find(r=>r.workspace===null&&r.source==='generated');if(name.includes('unavailable'))assert.ok(missing);
  }
});
test('missing projection and position are null, zero stays zero, old settings cannot win',()=>{
  let state=emptyPi();state=reducePi(state,{type:'settings',version:4} as any);state=reducePi(state,{type:'settings',version:2} as any);assert.equal(state.settings?.version,4);
  const turn:any={header:{settings:{emotion:{layers:[29],names:['calm']}}}},row:any={emotion:{proj:[[0]]}};
  assert.equal(projection(turn,row,29,'calm'),0);assert.equal(projection(turn,row,24,'calm'),null);assert.equal(projection(turn,{...row,emotion:null},29,'calm'),null);
  assert.equal(selectedRow(state,{request:'missing',pos:0}),null);
});
