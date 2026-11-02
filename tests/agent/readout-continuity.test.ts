import assert from 'node:assert/strict';
import http from 'node:http';
import {rm} from 'node:fs/promises';
import {test} from 'node:test';
import {BACKGROUND_CONTEXT as ctx,MemoryStorage} from 'glimmer-agent/pi';
import {emptyContent} from 'glimmer-agent/profile-model';
import type {FileBroker} from 'glimmer-agent/file-tools';
import type {BridgeMessage} from 'glimmer-agent/bridge';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import type {ToolAvailability} from '../../src/lib/server/file-broker.ts';
import {generationFor} from '../../src/lib/pi/generation.ts';
import {emptyPi,messagesOf,projection,reducePi,selectedRow,textOf,type PiView} from '../../src/lib/pi/model.ts';
import {fromLive,selectedPosition} from '../../src/lib/workspace/model.ts';
import {fakeKernel,until,rejectRootProbe} from '../live/fixture.ts';

const source='Synthetic file contents: never compiled or executed.';
const plan=[
  {name:'write',args:{path:'fixture.mojo',content:source}},
  {name:'mojo',args:{path:'fixture.mojo'}}
];

/** Reuse the live producer's readout frames; add the tool-call seam used by
 * tests/files/fixture.ts and tests/mojo/integration.test.ts. Both HTTP servers
 * bind loopback on ephemeral ports. The broker below is deliberately fake. */
async function toolReadoutKernel(missingFirstSummary=false){
  const f=await fakeKernel();let round=0;
  const errors:unknown[]=[];
  const server=http.createServer(async(req,res)=>{
    try{
      if(rejectRootProbe(req,res))return;
      assert.equal(req.method,'POST');assert.equal(req.url,'/v1/chat/completions');
      const index=round++,call=plan[index];assert.ok(index<3,'Unexpected extra model request');
      let body='';for await(const part of req){body+=part;assert.ok(body.length<1_000_000);}
      f.reply({answer:call?'':'Synthetic task complete.',finish:call?'tool_calls':'stop'});
      f.fault(missingFirstSummary&&index===0?'summary':'');
      const upstream=await fetch(f.base+'/v1/chat/completions',{
        method:'POST',headers:{'Content-Type':'application/json'},body,signal:AbortSignal.timeout(5000)
      });
      assert.equal(upstream.status,200);assert.ok(upstream.body);
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      const decoder=new TextDecoder();let pending='';
      for await(const bytes of upstream.body){
        pending+=decoder.decode(bytes,{stream:true});
        let end;
        while((end=pending.indexOf('\n\n'))!==-1){
          const frame=pending.slice(0,end);pending=pending.slice(end+2);
          if(frame==='data: [DONE]'){res.write(frame+'\n\n');continue;}
          assert.ok(frame.startsWith('data: '));
          const chunk=JSON.parse(frame.slice(6));
          // Distinct synthetic values make accidental cross-request borrowing
          // observable even though every request reuses positions 0 through 42.
          for(const record of chunk.nomos?.readout??[]){
            record.emotion.proj=record.emotion.proj.map((layer:number[])=>layer.map(value=>value+index*100));
            if(call&&record.channel==='answer')record.channel='tool';
          }
          if(call&&chunk.choices[0].finish_reason==='tool_calls'){
            chunk.choices[0].delta={tool_calls:[{index:0,id:`synthetic_${call.name}_${index}`,type:'function',
              function:{name:call.name,arguments:JSON.stringify(call.args)}}]};
          }
          res.write(`data: ${JSON.stringify(chunk)}\n\n`);
        }
      }
      pending+=decoder.decode();assert.equal(pending,'');res.end();
    }catch(error){errors.push(error);if(!res.headersSent)res.writeHead(500);res.end();}
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {...f,errors,base:`http://127.0.0.1:${(server.address() as import('node:net').AddressInfo).port}`,
    async close(){server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));await f.close();}};
}

function heldBroker(){
  const held=plan.map(()=>Promise.withResolvers<void>());
  const calls:{conversation:number;tool:string;args:Record<string,unknown>}[]=[];
  const files=new Map<string,string>();
  const broker:FileBroker & {status():ToolAvailability;close():Promise<void>}={
    async ready(){},
    status:()=>({available:true,reason:'Synthetic test broker; no isolation or compiler claim.',policy:'synthetic-only',
      mojo:{available:true,reason:'Synthetic result; no compiler runs.'}}),
    async execute(conversation,tool,args,signal){
      const index=calls.length;assert.ok(index<held.length);signal?.throwIfAborted();
      calls.push({conversation,tool,args:structuredClone(args)});
      const cancel=()=>held[index].reject(signal?.reason??Error('Aborted'));
      signal?.addEventListener('abort',cancel,{once:true});
      try{
        await held[index].promise;signal?.throwIfAborted();
        if(tool==='write'){
          assert.deepEqual(args,plan[0].args);files.set(String(args.path),String(args.content));
          return {ok:true,text:'SYNTHETIC_WRITE_RECORDED'};
        }
        assert.equal(tool,'mojo');assert.deepEqual(args,plan[1].args);assert.equal(files.get(String(args.path)),source);
        return {ok:true,text:JSON.stringify({synthetic:true,compile:{exit_code:0,reason:null,stdout:'',stderr:''},
          run:{exit_code:0,reason:null,stdout:'SYNTHETIC_MOJO_RESULT',stderr:''}})};
      }finally{signal?.removeEventListener('abort',cancel);}
    },
    async close(){for(const gate of held)gate.resolve();}
  };
  return {broker,calls,release:(index:number)=>held[index].resolve(),releaseAll:()=>held.forEach(gate=>gate.resolve())};
}

function checkTurn(state:PiView,index:number,status:'complete'|'contract_error'='complete'){
  const turn=state.turns[index];assert.ok(turn);assert.equal(turn.status,status);
  assert.equal(turn.turn,index);assert.ok(Number.isSafeInteger(turn.task));
  assert.equal(turn.header?.response_id,`chatcmpl-${index+1}`);assert.equal(turn.header?.turn,index);
  assert.equal(turn.records.length,43);assert.equal(new Set(turn.records.map(row=>row.pos)).size,43);
  assert.equal(turn.records.filter(row=>row.emotion).length,43);
  assert.equal(turn.records.filter(row=>row.workspace).length,40);
  assert.deepEqual(turn.header?.settings?.emotion.layers,[20,24,29]);
  assert.deepEqual(turn.header?.settings?.workspace?.layers,[44]);
  assert.equal(turn.header?.settings?.workspace?.k,4);
  assert.equal(turn.summary?.counts.records,status==='complete'?43:undefined);
  assert.equal(turn.summary?.counts.emotion_valid,status==='complete'?43:undefined);
  assert.equal(turn.summary?.counts.workspace_valid,status==='complete'?40:undefined);
  const chosen=selectedRow(state,{request:turn.request,pos:42});assert.ok(chosen);
  assert.equal(chosen.turn.request,turn.request);assert.ok(chosen.record.workspace);
  assert.equal(projection(chosen.turn,chosen.record,29,'calm'),12+index*100);
  assert.equal(chosen.record.channel,index<2?'tool':'answer');
  return turn;
}

test('real Pi retains attributed emotion/workspace rows through model → write → model → mojo → model and bridge reconnect',
  {timeout:15000},async()=>{
    const f=await toolReadoutKernel(),fake=heldBroker();
    for(const method of ['GET','HEAD'])assert.equal((await fetch(f.base,{method})).status,404);
    assert.deepEqual(f.errors,[]);
    let runtime:Awaited<ReturnType<typeof createRuntime>>|undefined;
    try{
      runtime=await createRuntime(f.base,new MemoryStorage(),fake.broker);const r=runtime;
      const id=await r.create(),profile=await r.profile(id);
      const saved=await r.saveProfile(id,profile.version,{...emptyContent('Synthetic readout continuity test.'),tools:['write','mojo']});
      await r.readout(id,{enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[20,24,29]},
        workspace:{lens:f.assets.lenses[0].id,layers:[44],k:4}},f.assets);
      let state=emptyPi();const wire:BridgeMessage[]=[];
      const bridge=await r.bridge(id,async message=>{wire.push(message);state=reducePi(state,message);});
      state=reducePi(state,{type:'bridge.snapshot',snapshot:bridge.snapshot});
      await r.submit(id,'Write the synthetic fixture, run the fake Mojo tool, then report.');

      await until(()=>fake.calls.length===1&&state.turns[0]?.status==='complete');
      const first=structuredClone(checkTurn(state,0));assert.equal(state.busy,true);
      assert.equal(f.requests.length,1);assert.equal(state.entries.filter(e=>e.kind==='pi.tool-result').length,0);
      assert.equal(selectedPosition(fromLive(first,{},''),null,'answer'),undefined,'Tool-only rows must not become answer rows');
      assert.ok(selectedPosition(fromLive(first,{},''),null,'tool')?.workspace);
      fake.release(0);

      await until(()=>fake.calls.length===2&&state.turns[1]?.status==='complete');
      checkTurn(state,1);assert.deepEqual(checkTurn(state,0),first);assert.equal(state.busy,true);
      assert.equal(f.requests.length,2);assert.equal(state.entries.filter(e=>e.kind==='pi.tool-result').length,1);
      assert.ok(f.requests[1].messages.some((message:any)=>message.role==='tool'&&message.tool_call_id==='synthetic_write_0'
        &&message.content.includes('SYNTHETIC_WRITE_RECORDED')));
      fake.release(1);

      await until(()=>state.turns.length===3&&state.turns[2]?.status==='complete'&&!state.busy&&!r.busy());
      for(let i=0;i<3;i++)checkTurn(state,i);
      assert.deepEqual(checkTurn(state,0),first);assert.deepEqual(state.errors,[]);assert.deepEqual(f.errors,[]);
      assert.equal(new Set(state.turns.map(turn=>turn.request)).size,3);
      assert.equal(new Set(state.turns.map(turn=>turn.task)).size,3,'Pi hands each tool round to a successor GenerationTask');
      assert.equal(state.turns.flatMap(turn=>turn.records).length,129);
      assert.deepEqual(f.requests.map(request=>request.nomos.readout.prompt),['all','new','new']);
      assert.deepEqual(f.requests.map(request=>request.nomos.turn),[0,1,2]);
      assert.ok(f.requests.every(request=>request.nomos.conversation===id&&request.max_tokens===6144));
      assert.ok(f.requests.every(request=>request.tools.map((tool:any)=>tool.function.name).join(',')==='write,mojo'));
      assert.ok(f.requests[2].messages.some((message:any)=>message.role==='tool'&&message.tool_call_id==='synthetic_mojo_1'
        &&message.content.includes('SYNTHETIC_MOJO_RESULT')));
      assert.deepEqual(fake.calls.map(call=>[call.conversation,call.tool]),[[Number(id),'write'],[Number(id),'mojo']]);

      const conversation=(await r.harness.conversation(Number(id) as any,ctx))!;
      const entries=(await conversation.entries({order:'ascending'},100,undefined,ctx)).items;
      assert.equal(entries.filter(entry=>entry.kind==='glimmer.readout.turn').length,3);
      assert.equal(entries.filter(entry=>entry.kind==='pi.tool-result').length,2);
      const assistants=entries.filter(entry=>entry.kind==='pi.assistant');assert.equal(assistants.length,3);
      assert.deepEqual(assistants.map(entry=>(entry.model?.[0] as any).stopReason),['toolUse','toolUse','stop']);
      for(const [index,turn] of state.turns.entries()){
        assert.equal(assistants[index].byTaskId,turn.task);
        assert.equal(generationFor(entries,turn.task,turn.request)?.request,turn.request);
        assert.equal(generationFor(entries,turn.task,turn.request)?.parameters.max_tokens,6144);
        const used=entries.find(entry=>entry.kind==='glimmer.profile.used'&&(entry.data as any).task===turn.task)?.data as any;
        assert.equal(used?.profile.digest,saved.active?.digest);
        assert.ok(wire.some(message=>message.type==='readout.batch'&&message.batch.request===turn.request));
        assert.ok(wire.some(message=>message.type==='readout.turn'&&message.turn.request===turn.request));
      }
      assert.equal(messagesOf(state).filter(entry=>entry.message.role==='assistant').map(entry=>textOf(entry.message)).join(''),
        'Synthetic task complete.');
      assert.equal(selectedRow(state,{request:'not-a-real-request',pos:42}),null,'An invalid pin must not borrow another request');
      const awaiting=reducePi(state,{type:'readout.batch',batch:{request:'synthetic-prompt-only',turn:3,
        header:{...state.turns[2].header!,turn:3,response_id:'synthetic-next'},records:[structuredClone(state.turns[2].records[0])]},entryId:999} as any);
      assert.equal(selectedRow(awaiting)?.turn.request,state.turns[2].request,
        'Follow mode retains the prior measured request while the next request has only prompt rows');
      assert.equal(selectedRow(awaiting,{request:'synthetic-prompt-only',pos:42}),null,
        'A pin to an unmeasured new-request position must not borrow the prior measurement');
      await bridge.stop();
      const restored=await r.bridge(id,async()=>{});
      const replay=reducePi(emptyPi(),{type:'bridge.snapshot',snapshot:restored.snapshot});
      assert.deepEqual(replay.turns,state.turns);
      for(let i=0;i<3;i++)checkTurn(replay,i);
      await restored.stop();
    }finally{fake.releaseAll();await runtime?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
  });

test('a tool request missing its summary retains both readout kinds as incomplete; later model requests cannot relabel it',
  {timeout:15000},async()=>{
    const f=await toolReadoutKernel(true),fake=heldBroker();fake.releaseAll();
    let runtime:Awaited<ReturnType<typeof createRuntime>>|undefined;
    try{
      runtime=await createRuntime(f.base,new MemoryStorage(),fake.broker);const r=runtime;
      const id=await r.create(),profile=await r.profile(id);
      await r.saveProfile(id,profile.version,{...emptyContent(),tools:['write','mojo']});
      await r.readout(id,{enabled:true,emotion:{directions:f.assets.directions[0].id,layers:[20,24,29]},
        workspace:{lens:f.assets.lenses[0].id,layers:[44],k:4}},f.assets);
      let state=emptyPi();const bridge=await r.bridge(id,async message=>{state=reducePi(state,message);});
      state=reducePi(state,{type:'bridge.snapshot',snapshot:bridge.snapshot});
      await r.submit(id,'Exercise the synthetic tool round with a missing first summary.');
      await until(()=>state.turns.length===3&&state.turns[2]?.status==='complete'&&!state.busy&&!r.busy());
      const incomplete=checkTurn(state,0,'contract_error');assert.ok(incomplete.error);assert.equal(incomplete.summary,null);
      checkTurn(state,1);checkTurn(state,2);
      assert.deepEqual(f.requests.map(request=>request.nomos.readout.prompt),['all','all','new']);
      assert.equal(new Set(state.turns.map(turn=>turn.request)).size,3);assert.deepEqual(f.errors,[]);
      await bridge.stop();
      const restored=await r.bridge(id,async()=>{});
      const replay=reducePi(emptyPi(),{type:'bridge.snapshot',snapshot:restored.snapshot});
      assert.deepEqual(checkTurn(replay,0,'contract_error'),incomplete);await restored.stop();
    }finally{fake.releaseAll();await runtime?.close();await f.close();await rm(f.root,{recursive:true,force:true});}
  });
