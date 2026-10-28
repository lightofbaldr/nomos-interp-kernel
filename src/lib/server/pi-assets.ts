import {readFile,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import type {Assets} from '../pi/model.ts';

const hash=(b:Uint8Array)=>createHash('sha256').update(b).digest('hex');
async function smallFile(file:string,max:number){const info=await stat(file);if(!info.isFile()||info.size>max)throw Error('Asset metadata exceeds its limit.');return readFile(file);}
export async function assetCatalog(registryPath:string,health:{readout?:{supported?:boolean;assets?:string[]}},tokenizer:string|null):Promise<Assets>{
  const result:Assets={supported:health.readout?.supported===true,error:null,directions:[],lenses:[],tokenizer};
  if(!result.supported){result.error='The running serve does not advertise readout support.';return result;}
  try{
    const registry=JSON.parse((await smallFile(registryPath,1_000_000)).toString());
    if(registry.v!==1||!Array.isArray(registry.assets)||registry.assets.length>64)throw Error('Invalid asset registry.');
    for(const a of registry.assets){
      if(!health.readout?.assets?.includes(a.id))continue;
      const bytes=await smallFile(path.join(a.dir,'manifest.json'),1_000_000);
      if(hash(bytes)!==a.id)throw Error('An asset manifest differs from its advertised ID.');
      const m=JSON.parse(bytes.toString()),layers=Object.keys(m.layers??{}).map(Number).sort((a,b)=>a-b);
      if(m.kind!==a.kind||layers.length===0||layers.some(l=>!Number.isInteger(l)||l<0||l>51))throw Error('Invalid asset layers.');
      if(a.kind==='directions'){
        if(!Array.isArray(m.names)||m.names.length!==m.E||!m.names.every((n:unknown)=>typeof n==='string')||new Set(m.names).size!==m.names.length)throw Error('Invalid direction names.');
        result.directions.push({id:a.id,names:m.names,layers});
      }else if(a.kind==='lens')result.lenses.push({id:a.id,layers});
    }
    if(!result.directions.length)throw Error('No local direction manifest matches the running serve.');
  }catch(e){result.supported=false;result.error=e instanceof Error&&!('code' in e)?e.message:'Asset metadata unavailable.';result.directions=[];result.lenses=[];}
  return result;
}

/** Isolated-token ByteLevel labels, not a reconstructed generated transcript. Unknown remains null. */
export function byteLevelLabels(tokenizer:Record<string,any>){
  if(tokenizer.model?.type!=='BPE'||tokenizer.decoder?.type!=='ByteLevel')throw Error('Only the pinned ByteLevel tokenizer is supported.');
  const values=[...Array.from({length:94},(_,i)=>33+i),...Array.from({length:12},(_,i)=>161+i),...Array.from({length:82},(_,i)=>174+i)];
  const byteMap=new Map(values.map(b=>[String.fromCodePoint(b),b]));let extra=0;
  for(let b=0;b<256;b++)if(!values.includes(b))byteMap.set(String.fromCodePoint(256+extra++),b);
  const labels=new Map<number,string>();
  for(const [text,id] of Object.entries(tokenizer.model.vocab)){
    if(!Number.isSafeInteger(id)||Number(id)<0)throw Error('Invalid tokenizer ID.');
    const bytes=[...text].map(c=>byteMap.get(c));
    if(bytes.some(b=>b===undefined))throw Error('Unexpected byte-level vocabulary character.');
    labels.set(Number(id),Buffer.from(bytes as number[]).toString('utf8'));
  }
  for(const t of tokenizer.added_tokens??[]){if(!Number.isSafeInteger(t.id)||typeof t.content!=='string')throw Error('Invalid added token.');labels.set(t.id,t.content);}
  return labels;
}
export async function loadLabels(file:string,pin:string){
  if(!/^[a-f0-9]{64}$/.test(pin))throw Error('A tokenizer SHA-256 pin is required.');
  const bytes=await smallFile(file,32_000_000);
  if(hash(bytes)!==pin)throw Error('Tokenizer does not match its operator pin.');
  return {sha256:pin,labels:byteLevelLabels(JSON.parse(bytes.toString()))};
}
