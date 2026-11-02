import {FILE_TOOLS,emptyContent,type ProfileContent} from 'glimmer-agent/profile-model';
import type {ReadoutConfig} from 'glimmer-agent/extension';
import type {Assets} from '../pi/model.ts';
import {emotionDirections} from '../pi/assets.ts';

export const AGENT_TOOLS=[...FILE_TOOLS,'mojo'] as const;
export const BASE_INSTRUCTIONS='You are a helpful AI assistant.\nReasoning strength: high.';
export function agentEnabled(content:ProfileContent|undefined|null){
  return Boolean(content&&AGENT_TOOLS.every(tool=>content.tools?.includes(tool)));
}
export function agentContent(content:ProfileContent,enabled:boolean):ProfileContent{
  if(typeof enabled!=='boolean')throw Error('Agent mode must be a boolean.');
  // Only the reviewed file and Mojo capabilities. A future shell is never
  // implicitly enabled by this switch, including when re-enabling a profile.
  return {...structuredClone(content),tools:enabled?[...AGENT_TOOLS]:[]};
}
export function baseAgentContent(active:ProfileContent){
  return {...emptyContent(BASE_INSTRUCTIONS),tools:[...(active.tools??[])]};
}
export function agentReadout(assets:Assets,current:ReadoutConfig):ReadoutConfig{
  const emotion=emotionDirections(assets);
  const savedLens=assets.lenses.find(l=>l.id===current.workspace?.lens);
  // An ambiguous catalog is not resolved by registry order.
  const lens=savedLens??(assets.lenses.length===1?assets.lenses[0]:undefined);
  if(!assets.supported||!emotion?.layers.includes(29)||!lens?.layers.includes(44))throw Error('Agent mode needs a verified emotion asset at L29 and an unambiguous workspace lens at L44. Choose the readout assets first.');
  return {enabled:true,
    emotion:current.enabled&&current.emotion?.directions===emotion.id?structuredClone(current.emotion):{directions:emotion.id,layers:[29]},
    workspace:current.enabled&&current.workspace?.lens===lens.id?structuredClone(current.workspace):{lens:lens.id,layers:[44],k:16}};
}
