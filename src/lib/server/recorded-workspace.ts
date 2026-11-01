import {open,realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {homedir} from 'node:os';
import {loadLabels} from './pi-assets.ts';
import {validateWorkspaceTurn,type WorkspaceTurn} from '../workspace/model.ts';

// Metadata only. Capture bytes remain outside the repository and build output.
export const recordings=[
  {id:'sietti-base',title:'Sietti paper · unsteered',file:'base.s0.free.json',sha256:'2db4c59e4a1deb6c6998856ede0f0dccb50f8771e151d85c660e80f0011553d6'},
  {id:'sietti-afraid',title:'Sietti paper · afraid +0.5',file:'fear_0.5.s0.free.json',sha256:'0d9c76b55f604c455ff3153d2d9b513893c89e9fdbea4ca5adfdf925118bc4d7'}
];
const root=path.join(homedir(),'nomos_data/steering-diff/run-20261027T090425-ws-SIETTI/SIETTI-reason/runs');
const tokenizer=path.join(homedir(),'nomos_data/muse-glimmer-tokenizer/tokenizer.json');
const tokenizerPin='c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d';
export async function recording(id:string):Promise<WorkspaceTurn>{
  const item=recordings.find(r=>r.id===id);if(!item)throw Error('Unknown recording.');
  const filename=path.join(root,item.file);
  if(await realpath(filename)!==filename)throw Error('Symlinked recording refused.');
  const f=await open(filename,constants.O_RDONLY|constants.O_NOFOLLOW);
  let bytes:Buffer;
  try{const stat=await f.stat();if(!stat.isFile()||stat.size>16_000_000)throw Error('Invalid recording size.');bytes=await f.readFile();}
  finally{await f.close();}
  if(createHash('sha256').update(bytes).digest('hex')!==item.sha256)throw Error('Recording identity changed.');
  const d=JSON.parse(bytes.toString()),header=d.summary?.header;
  if(d.identity?.tokenizer_sha256!==tokenizerPin||!header)throw Error('Recording tokenizer/header mismatch.');
  const vocab=await loadLabels(tokenizer,tokenizerPin),ids=new Set<number>();
  for(const r of d.records){ids.add(r.token);for(const top of r.workspace?.top??[])for(const [token] of top)ids.add(token);}
  const s=header.steer;
  return validateWorkspaceTurn({id:item.id,title:item.title,source:'recorded',
    prompt:"Summarize the main findings and methodology of the paper 'Recursive Compression for Anomaly Detection' by Sietti et al. (2019).",
    layers:header.settings.workspace?.layers??[],k:header.settings.workspace?.k??null,records:d.records,
    labels:Object.fromEntries([...ids].map(id=>[id,vocab.labels.get(id)??null])),
    steering:s?`${s.kind} ${s.s>=0?'+':''}${s.s} · L${s.layer}`:'Steering off',
    provenance:{label:'Recorded sampled capture · 27 Oct 2026 · sample 0',sha256:item.sha256,tokenizer:tokenizerPin,schedule:d.identity.schedule}});
}
