import test from 'node:test';
import assert from 'node:assert/strict';
import {systemProbe,parseMemory,parsePressure,parseGPUs,parseHealth} from '../../src/lib/server/system-probe.ts';
import {bytes,fraction} from '../../src/lib/inspection/system.ts';
import {statKeys} from '../../src/lib/live.ts';
const host={hostname:'ui-host',platform:'test',memory:null,pressure:null,disk:null,gpus:null};
const health={ready:true,model:'remote-model',spec:true,readout:{supported:true,assets:['a'.repeat(64)],steering:false}};
const stats=Object.fromEntries(statKeys.map(k=>[k,0]));
function fixture(){let busy=false,time=100000,fail=false;const calls:string[]=[];let hostCalls=0;
  const p=systemProbe({endpoint:'http://127.0.0.1:8092',busy:()=>busy,now:()=>time,host:async()=>{hostCalls++;return host;},fetcher:async input=>{calls.push(String(input));if(fail)throw Error('private diagnostic');return Response.json(String(input).endsWith('/health')?health:stats);}});
  return {p,calls,busy:(v:boolean)=>busy=v,advance:(ms:number)=>time+=ms,fail:()=>fail=true,hostCalls:()=>hostCalls};
}
test('plain snapshots never request cache or model endpoints; host is separate',async()=>{
  const f=fixture(),s=await f.p.get();assert.equal(s.host?.hostname,'ui-host');assert.equal(s.health?.model,'remote-model');assert.equal(s.cache.stats,null);
  assert.deepEqual(f.calls,['http://127.0.0.1:8092/health']);await f.p.get();assert.equal(f.calls.length,1);assert.equal(f.hostCalls(),1);
});
test('busy gate, cooldown and failed refresh preserve a real previous sample',async()=>{
  const f=fixture();f.busy(true);assert.equal((await f.p.get(true)).cache.state,'busy');assert.equal(f.calls.length,1);
  f.busy(false);const a=await f.p.get(true);assert.deepEqual(a.cache.stats,stats);assert.equal(a.cache.state,'fresh');assert.equal((await f.p.get(true)).cache.state,'cooldown');assert.equal(f.calls.length,2);
  f.advance(65000);f.fail();const b=await f.p.get(true);assert.equal(b.cache.state,'unavailable');assert.deepEqual(b.cache.stats,stats);assert.equal(b.cache.at,a.cache.at);assert.equal(JSON.stringify(b).includes('private diagnostic'),false);
  assert.equal((await f.p.get(true)).cache.state,'cooldown');
});
test('single flight refuses to queue a second cache query',async()=>{
  let release!:(r:Response)=>void,n=0;
  const p=systemProbe({endpoint:'http://localhost:8092',busy:()=>false,host:async()=>host,fetcher:async input=>{
    if(String(input).endsWith('/health'))return Response.json(health);n++;return new Promise(r=>release=r);
  }});
  const first=p.get(true);const second=await p.get(true);assert.equal(second.cache.state,'busy');assert.equal(n,1);release(Response.json(stats));assert.equal((await first).cache.state,'fresh');
});
test('invalid endpoints and malformed counters never become fabricated zeros',async()=>{
  for(const endpoint of ['http://evil.test','http://user:pass@127.0.0.1:8092','http://127.0.0.1:8092/arbitrary']){
    const p=systemProbe({endpoint,busy:()=>false,host:async()=>host,fetcher:async()=>{throw Error('must not fetch');}});const s=await p.get(true);assert.equal(s.endpoint,null);assert.equal(s.health,null);assert.equal(s.cache.stats,null);
  }
  const p=systemProbe({endpoint:'http://127.0.0.1:8092',busy:()=>false,host:async()=>host,fetcher:async()=>Response.json({prepares:0})});assert.equal((await p.get(true)).cache.state,'unavailable');
});
test('fixed-output parsers reject unavailable resources and preserve genuine zero',()=>{
  assert.deepEqual(parseMemory('MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n'),{total:1024000,available:409600,swapTotal:0,swapFree:0});
  assert.equal(parseMemory('MemTotal: 1000 kB'),null);
  assert.deepEqual(parsePressure('some avg10=1.23 avg60=0.0\nfull avg10=0.00 avg60=0.0'),{some:1.23,full:0});
  assert.equal(parsePressure(''),null);assert.equal(parseGPUs('NVIDIA, [N/A], [N/A], 0, 30'),null);
  assert.equal(parseGPUs('NVIDIA, 24000, 500, [N/A], 30')![0].utilization,null);
  assert.equal(parseHealth(health).steering,false);const absent=parseHealth({ready:true,model:'m'});assert.equal(absent.steering,null);assert.equal(absent.spec,null);assert.equal(absent.readout,null);assert.equal(absent.assets,null);
  assert.equal(bytes(null),'Unavailable');assert.equal(bytes(0),'0 B');assert.equal(fraction(0,0),'Not defined');assert.equal(fraction(0,10),'0.0%');
});
