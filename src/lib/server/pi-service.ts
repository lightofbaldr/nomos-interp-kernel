import {env} from '$env/dynamic/private';
import os from 'node:os';
import path from 'node:path';
import {createRuntime,checkedBase,type Runtime} from './pi-runtime.ts';
import {assetCatalog,loadLabels} from './pi-assets.ts';
import {createAnnotator} from './meta-annotator.ts';
import {readMetaKey,META_MODEL} from './meta-client.ts';
import {createFileBroker,FILE_BROKER_PATH} from './file-broker.ts';
import {installShutdown} from './shutdown.ts';
import {runtimeManager} from './pi-manager.ts';

// One harness per server process, never per HTTP request or browser attachment.
let manager:ReturnType<typeof runtimeManager>|undefined;
let vocabulary:ReturnType<typeof loadLabels>|undefined;
let stopping=false;
// Does not open a new harness just to report System status.
export async function piBusy(){return manager?manager.busy():false;}
installShutdown(async()=>{stopping=true;await manager?.close();});
const metaKey=()=>readMetaKey(env.GLIMMER_META_ENV_FILE||path.join(os.homedir(),'.env'));
export async function metaAvailability(){return {available:Boolean(await metaKey()),model:META_MODEL};}
export function metaAnnotator(runtime:Runtime){
  return createAnnotator(runtime.harness,{key:metaKey,labels:async turn=>{
    const setting=turn.header?.settings,pin=env.NOMOS_TOKENIZER_SHA256;
    if(!setting||!pin)return {labels:{},tokenizer:null};
    const ids=[setting.emotion.directions,...(setting.workspace?[setting.workspace.lens]:[])];
    const catalog=await assetCatalog(env.NOMOS_READOUT_REGISTRY||path.join(os.homedir(),'nomos_data/readout-assets/registry.json'),{readout:{supported:true,assets:ids}},pin);
    if(!catalog.supported||!catalog.directions.some(d=>d.id===setting.emotion.directions&&JSON.stringify(d.names)===JSON.stringify(setting.emotion.names))||setting.workspace&&!catalog.lenses.some(l=>l.id===setting.workspace!.lens))return {labels:{},tokenizer:null};
    try{
      vocabulary??=loadLabels(env.NOMOS_TOKENIZER_JSON||path.join(os.homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json'),pin).catch(e=>{vocabulary=undefined;throw e;});
      const v=await vocabulary,used=new Set(turn.records.flatMap(r=>r.source==='generated'?(r.workspace?.top??[]).flatMap(top=>top.map(x=>x[0])):[]));
      return {labels:Object.fromEntries([...used].map(id=>[id,v.labels.get(id)??null])),tokenizer:v.sha256};
    }catch{return {labels:{},tokenizer:null};}
  }});
}
function managed(){
  if(stopping)throw Error('Pi service is shutting down.');
  if(!env.NOMOS_API_BASE_URL)throw Error('No kernel endpoint is configured.');
  const db=env.GLIMMER_SESSION_DB||path.join(os.homedir(),'nomos_data/glimmer-lab/sessions.sqlite');
  const base=env.NOMOS_API_BASE_URL;
  if(!manager)console.info('Glimmer trusted file broker:',FILE_BROKER_PATH);
  return manager??=runtimeManager(path.resolve(db),()=>createRuntime(base,path.resolve(db),createFileBroker(path.resolve(db)+'.workspaces',env.GLIMMER_MOJO_PREFIX)));
}
export function withService<T>(fn:(runtime:Runtime)=>Promise<T>){return managed().use(fn);}
export const previewConversationPurge=(target:unknown)=>managed().preview(target);
export const purgeConversations=(target:unknown,revision:unknown,confirmation:unknown)=>managed().purge(target,revision,confirmation);
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
