import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {DEFAULT_STEERING_DATA,readSteeringFile} from '../src/lib/server/steering-data.ts';
import {parseIndex,parseBundle} from '../src/lib/steering-diff/model.ts';
const root=process.env.GLIMMER_STEERDIFF_DATA_DIR ?? DEFAULT_STEERING_DATA;
const hash=b=>createHash('sha256').update(b).digest('hex');
const cat=JSON.parse(await readSteeringFile(root,'catalog.json'));
const inventory=JSON.parse(await fs.readFile(path.resolve(root,'data-inventory.local.json')));
const byTarget=new Map(inventory.map(f=>[f.target,f]));assert.equal(byTarget.size,inventory.length);
let comparisons=0,files=0,bounds=0,divergent=0;
for(const d of cat.datasets){
  const idx=parseIndex(JSON.parse(await readSteeringFile(root,`${d.id}/index.json`)));
  const paths=['index.json',...idx.comparisons.map(c=>c.path)];
  assert.deepEqual(new Set(paths),new Set(Object.keys(d.hashes)));
  for(const rel of paths){
    const bytes=await readSteeringFile(root,`${d.id}/${rel}`),entry=byTarget.get(`${d.id}/${rel}`);
    assert.ok(entry);assert.equal(hash(bytes),entry.sha256);assert.equal(hash(await fs.readFile(entry.source)),entry.sha256);files++;
    if(rel==='index.json')continue;
    const ref=idx.comparisons.find(r=>r.path===rel),b=parseBundle(JSON.parse(bytes),idx,ref);comparisons++;
    if(b.comparison.divergence)divergent++;
    for(const p of b.comparison.positions)for(const l of p.workspace??[])bounds+=l.candidates.filter(c=>c.delta===null).length;
  }
}
assert.equal(comparisons,131);assert.equal(files,134);assert.equal(files,inventory.length);assert.ok(bounds>0&&divergent>0);
console.log('REAL BUNDLES VERIFIED:',comparisons,'comparisons;',files,'unchanged source/copy pairs.');
