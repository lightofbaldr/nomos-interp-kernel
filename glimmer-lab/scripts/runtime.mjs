// Exact, reviewed runtime. The npm Undici override cannot patch Node's built-in fetch.
export const SUPPORTED_NODE='24.22.1';
export const SUPPORTED_UNDICI='7.30.1';

/** @param {{node?:string, undici?:string}} [versions] */
export function assertSupportedRuntime(versions=process.versions){
  if(versions.node!==SUPPORTED_NODE||versions.undici!==SUPPORTED_UNDICI){
    throw Error(`Glimmer Lab requires Node ${SUPPORTED_NODE} with bundled Undici ${SUPPORTED_UNDICI}; found Node ${versions.node??'unknown'} / Undici ${versions.undici??'unknown'}. Use the installed supported runtime; no automatic installation or fallback.`);
  }
}
