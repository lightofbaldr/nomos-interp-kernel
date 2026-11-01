import {assertSupportedRuntime} from '../scripts/runtime.mjs';
import {env} from '$env/dynamic/private';
import type {Handle} from '@sveltejs/kit';
import {canonicalLoopbackPage} from '$lib/server/origin';
// Also cover direct adapter-node starts, not just the supported npm launcher.
assertSupportedRuntime();

export const handle:Handle=({event,resolve})=>{
  const location=canonicalLoopbackPage(event.request,event.url,env.ORIGIN);
  if(location)return new Response(null,{status:307,headers:{location,'Cache-Control':'no-store'}});
  return resolve(event);
};
