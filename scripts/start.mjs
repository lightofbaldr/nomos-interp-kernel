import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
console.log('GLIMMER_RUNTIME '+JSON.stringify({node:process.versions.node,undici:process.versions.undici,executable:process.execPath}));
// Set the default before adapter-node reads its environment. An explicit HOST
// remains the operator's opt-in; unset or empty must never expose private data.
process.env.HOST ||= '127.0.0.1';
await import('../build/index.js');
