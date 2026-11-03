import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRuntime} from '../../src/lib/server/pi-runtime.ts';
import {fakeKernel,until} from '../live/fixture.ts';
import {rm} from 'node:fs/promises';
test('history titles come from stored user messages and survive reopening the database',async()=>{
  const f=await fakeKernel();let runtime=await createRuntime(f.base,f.root+'/history.sqlite');
  try{
    const first=await runtime.create(),second=await runtime.create();
    assert.equal((await runtime.list()).find(c=>c.id===first)?.title,'New conversation');
    await runtime.submit(first,'  My first question\n stays saved  ');
    await until(async()=>!(await runtime.list()).some(c=>c.busy));
    await runtime.close();runtime=await createRuntime(f.base,f.root+'/history.sqlite');
    const list=await runtime.list();assert.equal(list.find(c=>c.id===first)?.title,'My first question stays saved');assert.equal(list.find(c=>c.id===second)?.title,'New conversation');
    assert.equal(f.requests.length,1,'Reading history never generates');
  }finally{await runtime.close();await f.close();await rm(f.root,{recursive:true,force:true});}
});
