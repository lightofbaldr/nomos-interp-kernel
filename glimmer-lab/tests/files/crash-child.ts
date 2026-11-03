// Test-only crash witness. The real broker commits the effect, then this process
// is killed before Pi can commit its tool result. No production hook enables it.
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {createFileBroker} from '../../src/lib/server/file-broker.ts';
import {emptyContent} from 'glimmer-agent/profile-model';
const [base,db,root]=process.argv.slice(2),files=createFileBroker(root);
const r=await createRuntime(base,db,{...files,execute:async(...args)=>{
  const result=await files.execute(...args);
  if(args[1]==='write'&&result.ok){process.stdout.write('EFFECT_COMMITTED\n');await new Promise<void>(()=>{setInterval(()=>{},1000);});}
  return result;
}});
const id=await r.create(),s=await r.profile(id);
await r.saveProfile(id,s.version,{...emptyContent('File task'),tools:['write']});
process.stdout.write(`CONVERSATION ${id}\n`);
await r.submit(id,'Write once.');
