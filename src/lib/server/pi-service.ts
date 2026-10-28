import {env} from '$env/dynamic/private';
import os from 'node:os';
import path from 'node:path';
import {createRuntime,checkedBase,type Runtime} from './pi-runtime.ts';
import {assetCatalog,loadLabels} from './pi-assets.ts';

// One harness per server process, never per HTTP request or browser attachment.
let pending:Promise<Runtime>|undefined;
let vocabulary:ReturnType<typeof loadLabels>|undefined;
export function service(){
  if(!env.NOMOS_API_BASE_URL)throw Error('No kernel endpoint is configured.');
  return pending??=createRuntime(env.NOMOS_API_BASE_URL,
    env.GLIMMER_SESSION_DB||path.join(os.homedir(),'nomos_data/glimmer-lab/sessions.sqlite')).catch(e=>{pending=undefined;throw e;});
}
export async function capabilities(){
  const base=checkedBase(env.NOMOS_API_BASE_URL??'');
  const response=await fetch(base+'/health',{signal:AbortSignal.timeout(5000),redirect:'error',headers:{Connection:'close'}});
  if(!response.ok)throw Error('Kernel health unavailable.');
  const h=await response.json();
  const tokenPin=env.NOMOS_TOKENIZER_SHA256||null;
  return {health:{ready:h.ready===true,model:String(h.model??'unknown'),engine:String(h.engine??'unknown'),spec:h.spec===true,store:h.kvstore?.status==='open'?'open':'unknown'},
    assets:await assetCatalog(env.NOMOS_READOUT_REGISTRY||path.join(os.homedir(),'nomos_data/readout-assets/registry.json'),h,tokenPin)};
}
export async function tokenLabels(ids:unknown){
  if(!Array.isArray(ids)||ids.length>2048||!ids.every(i=>Number.isSafeInteger(i)&&i>=0&&i<1_000_000))throw Error('Send at most 2048 token IDs.');
  const pin=env.NOMOS_TOKENIZER_SHA256;
  if(!pin)throw Error('Token labels unavailable: configure the tokenizer identity on the server. Raw IDs remain valid.');
  vocabulary??=loadLabels(env.NOMOS_TOKENIZER_JSON||path.join(os.homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json'),pin).catch(e=>{vocabulary=undefined;throw e;});
  const v=await vocabulary;
  return {sha256:v.sha256,labels:Object.fromEntries(ids.map(i=>[i,v.labels.get(i)??null]))};
}
