import {configured} from '$lib/server/nomos';
export const load=({url}:{url:URL})=>({liveConfigured:configured(),demo:url.searchParams.get('mode')==='demo'});
