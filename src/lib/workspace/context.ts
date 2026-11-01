import {getContext} from 'svelte';
import type {LiveState} from '../live';
export type LabContext={readonly live:LiveState};
export const LAB_CONTEXT='glimmer.lab.session';
export const labContext=()=>getContext<LabContext>(LAB_CONTEXT);
