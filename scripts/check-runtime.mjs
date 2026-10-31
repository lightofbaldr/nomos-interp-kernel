import {assertSupportedRuntime} from './runtime.mjs';
assertSupportedRuntime();
console.log('NODE RUNTIME VERIFIED '+JSON.stringify({node:process.versions.node,undici:process.versions.undici,executable:process.execPath}));
