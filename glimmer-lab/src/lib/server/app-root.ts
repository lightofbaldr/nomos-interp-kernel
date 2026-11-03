import {fileURLToPath} from 'node:url';
// Vite substitutes the build root. Direct TS tests derive it from this module, never cwd.
declare const __GLIMMER_APP_ROOT__:string;
export const APP_ROOT=typeof __GLIMMER_APP_ROOT__==='string'?__GLIMMER_APP_ROOT__:fileURLToPath(new URL('../../../',import.meta.url));
