import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
const root=new URL('../',import.meta.url);
const pkg=JSON.parse(await readFile(new URL('package.json',root),'utf8'));
const lock=JSON.parse(await readFile(new URL('package-lock.json',root),'utf8'));
assert.equal(pkg.overrides.undici,'6.29.1');
assert.equal(lock.packages[''].engines.node,'24.22.1');
const copies=Object.entries(lock.packages).filter(([name])=>/(^|\/)node_modules\/undici$/.test(name));
assert.ok(copies.length>0,'Positive control: Undici must actually be inventoried');
for(const [name,entry] of copies){
  assert.equal(entry.version,'6.29.1',name+' lock');
  assert.equal(JSON.parse(await readFile(new URL(name+'/package.json',root),'utf8')).version,'6.29.1',name+' installed');
}
console.log(`UNDICI DEPENDENCIES VERIFIED: ${copies.length} installed/locked copies at 6.29.1; built-in ${process.versions.undici}`);
