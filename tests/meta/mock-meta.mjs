// Test-process preload only. Production has no configurable provider endpoint.
const endpoint=new URL(process.env.TEST_META_ENDPOINT);
if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||endpoint.pathname!=='/')throw Error('Invalid fake Meta endpoint.');
const original=globalThis.fetch;
globalThis.fetch=(url,init)=>original(String(url)==='https://api.meta.ai/v1/chat/completions'?new URL('/v1/chat/completions',endpoint):url,init);
