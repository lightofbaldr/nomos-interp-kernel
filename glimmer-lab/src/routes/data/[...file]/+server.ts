import {env} from '$env/dynamic/private';
import {DEFAULT_STEERING_DATA,readSteeringFile} from '$lib/server/steering-data';
import type {RequestHandler} from './$types';

export const GET:RequestHandler = async ({params}) => {
  const headers={'Content-Type':'application/json; charset=utf-8','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
  try {
    const bytes=await readSteeringFile(env.GLIMMER_STEERDIFF_DATA_DIR ?? DEFAULT_STEERING_DATA,params.file ?? '');
    return new Response(new Uint8Array(bytes),{headers});
  } catch {
    // Never leak an absolute path, filesystem details, or substitute another file.
    return new Response(JSON.stringify({error:'Local recorded data unavailable or invalid.'}),{status:404,headers});
  }
};
