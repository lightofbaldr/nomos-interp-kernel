import type {Assets} from './model.ts';
export const SP1_DIRECTIONS='b3cddaec64724aacd7404b13593f50a12cc0581447aaf1ac6978ab77e19a9352';
/** Registry order is not semantics. Prefer the reviewed SP1 identity; an
 * alternative must unambiguously contain these named emotion directions.
 * Random-only assets are never an emotion default.
 */
export function emotionDirections(assets:Assets|undefined){
  const choices=assets?.directions??[],pinned=choices.find(d=>d.id===SP1_DIRECTIONS);
  if(pinned)return pinned;
  const named=choices.filter(d=>['desperate','afraid','angry'].every(n=>d.names.includes(n)));
  return named.length===1?named[0]:undefined;
}
