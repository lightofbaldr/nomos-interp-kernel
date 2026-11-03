import {PurgeConflict,previewPurge,executePurge} from './conversation-data.ts';
import type {Runtime} from './pi-runtime.ts';

/** All HTTP users (including paid annotations) hold a lease for their entire
 * operation. Maintenance claims synchronously; it never queues behind live work.
 * Streaming bridges are owned and closed by the runtime, not by this lease. */
export function runtimeManager(database:string,create:()=>Promise<Runtime>){
  let pending:Promise<Runtime>|undefined,leases=0,maintenance=false,stopping=false;
  const get=()=>pending??=create().catch(e=>{pending=undefined;throw e;});
  return {
    async use<T>(fn:(runtime:Runtime)=>Promise<T>):Promise<T>{
      if(stopping||maintenance)throw new PurgeConflict('Conversation maintenance is in progress. Try again when it finishes.');
      leases++;
      try{return await fn(await get());}finally{leases--;}
    },
    async busy(){return maintenance||leases>0||(pending?(await pending).busy():false);},
    async preview(value:unknown){return this.use(async()=>previewPurge(database,value));},
    async purge(value:unknown,revision:unknown,confirmation:unknown){
      if(stopping||maintenance||leases)throw new PurgeConflict('A conversation operation is still active. Wait before deleting data.');
      maintenance=true;
      try{
        const runtime=await get();
        await runtime.assertIdle();
        const p=await previewPurge(database,value);
        if(p.revision!==revision)throw new PurgeConflict('Conversations changed since confirmation opened. Review the deletion again.');
        if(p.confirmation!==confirmation)throw new PurgeConflict(`Type ${p.confirmation} to confirm.`);
        await runtime.close();pending=undefined;
        return await executePurge(database,value,revision,confirmation);
      }finally{maintenance=false;}
    },
    async close(){stopping=true;if(pending)await(await pending).close();}
  };
}
