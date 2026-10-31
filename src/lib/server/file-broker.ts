/** Fixed-argv trusted broker. Model input is JSON on stdin, never argv/code/env. */
import {spawn} from 'node:child_process';
import path from 'node:path';
import {APP_ROOT} from './app-root.ts';
import type {FileBroker} from 'glimmer-agent/file-tools';
import type {SandboxTool} from 'glimmer-agent/profile-model';
export type ToolAvailability={available:boolean;reason:string;policy:string;mojo?:{available:boolean;reason:string;compiler?:unknown}};
const POLICY='glimmer.files/bwrap-1';
export const FILE_BROKER_PATH=path.join(APP_ROOT,'runtime/files/broker.py');
export const MOJO_BROKER_PATH=path.join(APP_ROOT,'runtime/files/mojo_broker.py');
export function createFileBroker(root:string,mojoPrefix?:string){
  let state:ToolAvailability={available:false,reason:'Isolation has not been checked. Check availability before enabling tools.',policy:POLICY,mojo:{available:false,reason:mojoPrefix?'Compiler not checked.':'Configure GLIMMER_MOJO_PREFIX on the server.'}};
  let closing=false,poisoned=false,probe:Promise<void>|undefined,mojoProbe:Promise<void>|undefined;
  const children=new Set<{stop:()=>void;done:Promise<unknown>}>();
  async function invoke(id:string,request:unknown,signal?:AbortSignal,mojo=false){
    if(closing||poisoned)throw Error('Sandboxed tools unavailable until the server is restarted.');
    signal?.throwIfAborted();
    if(mojo&&!mojoPrefix)throw Error('Configure GLIMMER_MOJO_PREFIX on the server.');
    const input=JSON.stringify(request);if(Buffer.byteLength(input)>2097152)throw Error('File request exceeds 2 MiB.');
    let done!:Promise<any>,stop!:()=>void;
    done=new Promise((resolve,reject)=>{
      const env:NodeJS.ProcessEnv={PATH:'/usr/bin:/bin',LANG:'C.UTF-8'};
      for(const key of ['XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'])if(process.env[key])env[key]=process.env[key];
      const child=spawn('/usr/bin/python3',['-I','-B',mojo?MOJO_BROKER_PATH:FILE_BROKER_PATH,root,id,...(mojo?[mojoPrefix!]:[])],{env,stdio:['pipe','pipe','pipe'],shell:false});
      const chunks:Buffer[]=[];let bytes=0,stopped=false,error:Error|undefined,kill:ReturnType<typeof setTimeout>|undefined;
      stop=()=>{if(stopped)return;stopped=true;child.kill('SIGTERM');kill=setTimeout(()=>{poisoned=true;child.kill('SIGKILL');},15000);};
      const abort=()=>{error=Error('File operation interrupted; a mutation may have completed. Inspect before retrying.');stop();};
      const timer=setTimeout(()=>{error=Error('Sandbox broker deadline exceeded.');stop();},mojo?160000:75000);
      signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
      child.stdout.on('data',(b:Buffer)=>{bytes+=b.length;if(bytes>81920){error=Error('File broker output exceeded its bound.');stop();}else chunks.push(b);});
      child.stderr.on('data',(b:Buffer)=>{bytes+=b.length;if(bytes>81920){error=Error('File broker diagnostics exceeded their bound.');stop();}});
      child.stdin.on('error',()=>{});child.on('error',e=>{error=e;});
      child.once('close',code=>{clearTimeout(timer);clearTimeout(kill);signal?.removeEventListener('abort',abort);
        try{if(error)throw error;const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if(code!==0||result.unavailable||typeof result.ok!=='boolean'||result.policy!==(mojo?'glimmer.mojo/bwrap-1':POLICY))throw Error(result.error||'Sandbox unavailable.');
          resolve(result);
        }catch(e){const reason=e instanceof Error?e.message:'Sandbox unavailable.';state=mojo?{...state,mojo:{available:false,reason}}:{...state,available:false,reason};reject(e);}
      });
      child.stdin.end(input);
    });
    const active={stop:()=>stop(),done};children.add(active);
    try{return await done;}finally{children.delete(active);}
  }
  const broker:FileBroker & {status():ToolAvailability;close():Promise<void>}={
    status:()=>({...state}),
    async ready(tools=[]){
      probe??=invoke('probe',{}).then(()=>{state={...state,available:true,reason:'Isolated bwrap service and resource limits verified. Every operation rechecks them.',policy:POLICY};}).finally(()=>{probe=undefined;});
      await probe;
      if(tools.includes('mojo')){
        mojoProbe??=invoke('probe',{path:'.'},undefined,true).then(r=>{
          if(!r.ok)throw Error('Mojo compiler probe failed.');
          state={...state,mojo:{available:true,reason:'Compiler version command ran inside checked isolation; builds are checked per call.',compiler:r.result.compiler}};
        }).catch(e=>{state={...state,mojo:{available:false,reason:e.message}};throw e;}).finally(()=>{mojoProbe=undefined;});
        await mojoProbe;
      }
    },
    async execute(conversation:number,tool:SandboxTool,args:Record<string,unknown>,signal?:AbortSignal){
      if(!Number.isSafeInteger(conversation)||conversation<1||conversation>=1e15)throw Error('Invalid workspace identity.');
      const result=await invoke(String(conversation),tool==='mojo'?args:{op:tool,args},signal,tool==='mojo');
      return {ok:result.ok,text:JSON.stringify(tool==='mojo'||result.ok?result.result:{error:result.error})};
    },
    async close(){closing=true;const active=[...children];for(const c of active)c.stop();await Promise.allSettled(active.map(c=>c.done));}
  };
  return broker;
}
