// Display adapter only. Scientific values are supplied by steerdiff.compare.
export type Mode = 'matched' | 'free';
export type Side = 'baseline' | 'steered';
export interface Settings { emotion: { layers: number[]; names: string[] }; workspace: { layers: number[]; k: number } | null }
export interface Divergence { index: number; position: number; baseline_token: number | null; steered_token: number | null }
export interface Candidate {
  id: number; baseline: number | null; steered: number | null;
  baseline_rank: number | null; steered_rank: number | null;
  baseline_below: number | null; steered_below: number | null;
  delta: number | null; delta_min: number | null; delta_max: number | null;
  status: 'both' | 'entered' | 'left';
}
export interface Position {
  pos: number; token: number; source: 'prompt' | 'generated'; channel: string | null;
  missing: string[]; emotion_delta: (number | null)[][] | null;
  workspace: { layer: number; missing: string[]; candidates: Candidate[] }[] | null;
}
export interface Arm { name: string; kind: string | null; s: number | null }
export interface ScenarioSource {
  path: string; sha256: string; question: string; label: string | null; classifier: string | null;
  prompt_len: number; answer_start?: number; thinking_tail?: number; recorded_len?: number;
  [key: string]: unknown;
}
export interface Scenario {
  key: string; title: string; onset: number; modes: Mode[]; max_new: number | null;
  P: number; reference_len: number | null; source: ScenarioSource | null;
}
export interface ComparisonRef { prompt: string; arm: string; mode: Mode; path: string; divergence: Divergence | null; sample?: number }
export interface SuppliedLabel { label: string; note?: string; detail?: string }
export interface SuppliedLabels { by: string; baseline: SuppliedLabel | null; steered: SuppliedLabel | null }
export interface BundleIndex {
  schema: 'nomos.steerdiff.ui/1'; run: string; identity: Record<string, unknown>;
  settings: Settings; prompts: Record<string, string>; arms: Arm[]; display_k: number;
  gates: Record<string, Record<string, { pass: boolean; detail: string }>>;
  comparisons: ComparisonRef[];
  scenarios?: Record<string, Scenario>;
  labels_source?: string | null;
}
export interface Bundle {
  schema: 'nomos.steerdiff.ui/1'; prompt: string; arm: Arm | ArmRecord['arm']; mode: Mode;
  sample?: number; labels?: SuppliedLabels | null;
  scenario?: Scenario | null;
  vocab: Record<string, string>; text: Record<Side, { reasoning: string; answer: string }>;
  comparison: {
    schema: 'nomos.steerdiff/1'; mode: Mode; reference: string | null; P: number;
    display_k: number; settings: Settings; divergence: Divergence | null;
    tracks: Record<Side, number[]> | null; positions: Position[];
    baseline: ArmRecord; steered: ArmRecord;
  };
}
export interface ArmRecord { run_id: string; arm: { name: string; steer: null | { layer: number; kind: string; s: number; from_pos: number }; sample?: number; seed?: number }; identity: Record<string, unknown>; tokens: number[] }
export interface Dataset { id: string; run: string; index: string; hashes: Record<string, string>; title?: string }
export interface Catalog { schema: 'glimmer.local-catalog/1'; datasets: Dataset[] }

function requireThat(value: unknown, message: string): asserts value { if (!value) throw new Error(`Bundle refused: ${message}`); }
function finiteTree(x: unknown): void {
  if (typeof x === 'number') requireThat(Number.isFinite(x), 'non-finite number');
  else if (x && typeof x === 'object') for (const v of Object.values(x)) finiteTree(v);
}
const numOrNull = (x: unknown) => x === null || typeof x === 'number' && Number.isFinite(x);
const tokenId = (x: unknown) => Number.isInteger(x) && (x as number) >= 0;
export function safePath(path: string): boolean {
  return typeof path === 'string' && /^[a-zA-Z0-9_./-]+$/.test(path) && !path.startsWith('/') && path.split('/').every(p => p && p !== '.' && p !== '..');
}
function settingsValid(s: Settings) {
  requireThat(Array.isArray(s?.emotion?.names) && s.emotion.names.every(x => typeof x === 'string'), 'direction names');
  for (const layers of [s?.emotion?.layers, ...(s.workspace===null?[]:[s?.workspace?.layers])]) requireThat(Array.isArray(layers) && layers.length && layers.every(tokenId) && new Set(layers).size === layers.length, 'layer roster');
  requireThat(s.workspace===null || Number.isInteger(s.workspace.k) && s.workspace.k > 0, 'transmitted k');
}
function scenarioValid(s: Scenario, key: string) {
  requireThat(s && s.key === key && typeof s.title === 'string', 'scenario identity');
  requireThat(Array.isArray(s.modes) && s.modes.length > 0 && s.modes.every(m => m === 'matched' || m === 'free') && new Set(s.modes).size === s.modes.length, 'scenario modes');
  requireThat(tokenId(s.onset) && tokenId(s.P) && s.P > 0, 'scenario positions');
  requireThat(s.max_new === null || tokenId(s.max_new), 'scenario budget');
  requireThat(s.reference_len === null || tokenId(s.reference_len), 'scenario reference length');
  if (s.source !== null) {
    requireThat(s.source && typeof s.source.question === 'string' && typeof s.source.path === 'string' && /^[a-f0-9]{64}$/.test(s.source.sha256), 'scenario source identity');
    for (const k of ['label', 'classifier'] as const) requireThat(s.source[k] === null || typeof s.source[k] === 'string', `source ${k}`);
    requireThat(tokenId(s.source.prompt_len), 'source prompt_len');
    for (const k of ['answer_start', 'thinking_tail', 'recorded_len'] as const) requireThat(s.source[k]===undefined || tokenId(s.source[k]), `source ${k}`);
  }
}
export function parseIndex(raw: unknown): BundleIndex {
  const x = raw as BundleIndex;
  finiteTree(x);
  requireThat(x?.schema === 'nomos.steerdiff.ui/1' && typeof x.run === 'string', 'index schema');
  settingsValid(x.settings);
  requireThat(x.display_k > 0 && Number.isInteger(x.display_k) && (x.settings.workspace===null || x.display_k <= x.settings.workspace.k), 'display k');
  requireThat(x.prompts && Object.values(x.prompts).every(v => typeof v === 'string') && Array.isArray(x.arms), 'prompts and arms');
  requireThat(new Set(x.arms.map(a=>a.name)).size === x.arms.length && x.arms.every(a=>typeof a.name === 'string' && (a.kind===null || typeof a.kind==='string') && numOrNull(a.s)), 'arm roster');
  if (x.scenarios !== undefined) {
    requireThat(x.scenarios && typeof x.scenarios === 'object' && !Array.isArray(x.scenarios), 'scenario map');
    for (const [key, s] of Object.entries(x.scenarios)) { requireThat(key in x.prompts, 'scenario prompt'); scenarioValid(s, key); }
  }
  requireThat(Array.isArray(x.comparisons) && x.comparisons.length, 'comparison roster');
  requireThat(x.labels_source===undefined || x.labels_source===null || typeof x.labels_source==='string', 'labels source');
  const seen = new Set<string>(), paths=new Set<string>();
  for (const r of x.comparisons) {
    requireThat(r.mode === 'matched' || r.mode === 'free', 'mode');
    requireThat(safePath(r.path) && r.prompt in x.prompts && x.arms.some(a => a.name === r.arm), 'comparison reference');
    requireThat(!x.scenarios?.[r.prompt] || x.scenarios[r.prompt].modes.includes(r.mode), 'undeclared scenario mode');
    requireThat(r.sample===undefined || Number.isSafeInteger(r.sample) && r.sample>=0, 'sample index');
    if(r.sample!==undefined)requireThat(r.mode==='free' && r.path===`${r.prompt}/${r.arm}.s${r.sample}.free.json`, 'sample path identity');
    const key = `${r.prompt}/${r.arm}/${r.mode}/${r.sample??'unsampled'}`;
    requireThat(!seen.has(key), 'duplicate comparison'); seen.add(key);
    requireThat(!paths.has(r.path),'duplicate comparison path');paths.add(r.path);
  }
  return x;
}
export function parseBundle(raw: unknown, index: BundleIndex, ref: ComparisonRef): Bundle {
  const b = raw as Bundle, c = b?.comparison;
  finiteTree(b);
  requireThat(b?.schema === 'nomos.steerdiff.ui/1' && c?.schema === 'nomos.steerdiff/1', 'comparison schema');
  requireThat(b.prompt === ref.prompt && b.arm.name === ref.arm && b.mode === ref.mode && c.mode === ref.mode, 'selection identity');
  requireThat(b.sample===ref.sample, 'sample selection identity');
  const indexedArm=index.arms.find(a=>a.name===ref.arm);
  if(ref.sample===undefined){requireThat(jsonEqual(b.arm,indexedArm), 'arm settings identity');}
  else {
    requireThat(c.baseline.run_id===`${ref.prompt}.base.s${ref.sample}` && c.steered.run_id===`${ref.prompt}.${ref.arm}.s${ref.sample}`, 'sample run identity');
    requireThat(jsonEqual(b.arm,c.steered.arm) && c.baseline.arm.sample===ref.sample && c.steered.arm.sample===ref.sample, 'sample arm identity');
    requireThat(Number.isSafeInteger(c.baseline.arm.seed) && c.baseline.arm.seed!>=0 && c.steered.arm.seed===c.baseline.arm.seed, 'sample seed pairing');
    requireThat(c.baseline.arm.steer===null,'sample baseline is steered');
    const steer=c.steered.arm.steer;
    requireThat(indexedArm && (steer===null?indexedArm.kind===null && indexedArm.s===null:steer.kind===indexedArm.kind && steer.s===indexedArm.s && steer.layer===index.identity.steer_layer), 'arm settings identity');
  }
  if(b.labels!==undefined && b.labels!==null){
    requireThat(ref.sample!==undefined && typeof b.labels.by==='string' && b.labels.by.trim(), 'label attribution');
    for(const side of ['baseline','steered'] as const){const label=b.labels[side];requireThat(label===null || label && typeof label==='object' && !Array.isArray(label) && typeof label.label==='string' && label.label.length>0 && ['note','detail'].every(k=>label[k as 'note'|'detail']===undefined || typeof label[k as 'note'|'detail']==='string'),`label ${side}`);}
  }
  const scenario = index.scenarios?.[ref.prompt] ?? null;
  requireThat(jsonEqual(b.scenario ?? null, scenario), 'scenario provenance mismatch');
  if (scenario) {
    requireThat(c.P === scenario.P && scenario.modes.includes(c.mode), 'scenario prefill or mode mismatch');
    requireThat(c.steered.arm.steer === null || c.steered.arm.steer.from_pos === scenario.onset, 'scenario onset mismatch');
  }
  requireThat(c.display_k === index.display_k && JSON.stringify(c.settings) === JSON.stringify(index.settings), 'settings identity');
  requireThat(JSON.stringify(c.divergence) === JSON.stringify(ref.divergence), 'divergence identity');
  requireThat(Number.isInteger(c.P) && c.P > 0 && Array.isArray(c.positions) && c.positions.length, 'position roster');
  requireThat(c.steered.arm.name === ref.arm && c.baseline.arm.name === 'base', 'arm identity');
  requireThat(b.vocab && Object.values(b.vocab).every(t => typeof t === 'string'), 'vocabulary');
  for (const side of ['baseline', 'steered'] as const) {
    requireThat(typeof b.text?.[side]?.answer === 'string' && typeof b.text?.[side]?.reasoning === 'string', 'recorded text');
    requireThat(Array.isArray(c[side].tokens) && c[side].tokens.every(tokenId), 'token roster');
    requireThat(c.tracks === null && c.mode === 'matched' || c.tracks && Array.isArray(c.tracks[side]) && c.tracks[side].every(tokenId), 'token tracks');
  }
  if (c.mode === 'matched') requireThat(c.divergence === null && (c.tracks === null || c.tracks.baseline.length === 0 && c.tracks.steered.length === 0), 'matched replay has no divergence');
  if (c.divergence) requireThat(c.mode === 'free' && c.divergence.position === c.P + c.divergence.index, 'divergence position');
  let previous = -1;
  for (const p of c.positions) {
    requireThat(Number.isInteger(p.pos) && p.pos > previous && tokenId(p.token), 'ordered positions'); previous = p.pos;
    requireThat(!c.divergence || p.pos < c.divergence.position, 'paired row at/after divergence');
    requireThat(p.source === (p.pos < c.P ? 'prompt' : 'generated'), 'position source');
    requireThat(Array.isArray(p.missing), 'missing flags');
    requireThat(p.emotion_delta === null || p.emotion_delta.length === c.settings.emotion.layers.length && p.emotion_delta.every(row => Array.isArray(row) && row.length === c.settings.emotion.names.length && row.every(numOrNull)), 'direction shape');
    if(c.settings.workspace===null)requireThat(p.workspace===null,'workspace supplied when disabled');
    if (p.workspace === null) continue;
    requireThat(c.settings.workspace!==null,'workspace settings missing');
    requireThat(Array.isArray(p.workspace) && p.workspace.length === c.settings.workspace.layers.length, 'workspace shape');
    for (const [li, layer] of p.workspace.entries()) {
      requireThat(layer.layer === c.settings.workspace.layers[li] && Array.isArray(layer.missing) && Array.isArray(layer.candidates), 'workspace layers');
      const ids = new Set<number>();
      for (const a of layer.candidates) {
        requireThat(tokenId(a.id) && !ids.has(a.id), 'duplicate or invalid candidate'); ids.add(a.id);
        for (const key of ['baseline', 'steered', 'baseline_below', 'steered_below', 'delta', 'delta_min', 'delta_max'] as const) requireThat(numOrNull(a[key]), `candidate ${key}`);
        for (const side of ['baseline', 'steered'] as const) {
          const rank = a[`${side}_rank`];
          requireThat(rank === null || Number.isInteger(rank) && rank > 0 && rank <= c.settings.workspace.k, 'rank outside transmitted set');
          requireThat((rank === null) === (a[side] === null), 'unknown score/rank mismatch');
        }
        requireThat(['entered', 'left', 'both'].includes(a.status), 'candidate status');
        requireThat(a.delta === null || a.baseline !== null && a.steered !== null, 'exact delta with unknown operand');
      }
    }
  }
  return b;
}

export function topCandidates(candidates: Candidate[], side: Side, k: number): Candidate[] {
  return candidates.filter(c => c[`${side}_rank`] !== null && c[`${side}_rank`]! <= k).toSorted((a, b) => a[`${side}_rank`]! - b[`${side}_rank`]!);
}
export function differenceGroups(candidates: Candidate[]) {
  // Bounds have no exact total order. Do not pretend otherwise.
  return {
    exact: candidates.filter(c => c.delta !== null).toSorted((a,b) => b.delta! - a.delta!),
    bounded: candidates.filter(c => c.delta === null).toSorted((a,b) => a.id - b.id),
  };
}
export function numberText(n: number | null | undefined, signed = false): string {
  if (n === null || n === undefined) return 'Unknown';
  if (!Number.isFinite(n)) throw new Error('Non-finite display value');
  if (n === 0) return '0.00';
  if (Math.abs(n) < .005) return `${signed && n > 0 ? '+' : ''}${n.toExponential(2)}`;
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(2)}`;
}
export function scoreText(c: Candidate, side: Side): string {
  return c[side] !== null ? numberText(c[side]) : c[`${side}_below`] !== null ? `≤ ${boundText(c[`${side}_below`]!, false)}` : 'Unknown';
}
export function boundText(n: number, lower: boolean, signed = false): string {
  // Outward display rounding preserves the inequality. This is not a new estimate.
  return numberText((lower ? Math.floor(n*100) : Math.ceil(n*100))/100, signed);
}
export function deltaText(c: Candidate): string {
  if (c.delta !== null) return numberText(c.delta, true);
  if (c.delta_min !== null && c.delta_max !== null) return `[${boundText(c.delta_min, true, true)}, ${boundText(c.delta_max, false, true)}]`;
  if (c.delta_min !== null) return `≥ ${boundText(c.delta_min, true, true)}`;
  if (c.delta_max !== null) return `≤ ${boundText(c.delta_max, false, true)}`;
  return 'Unknown';
}
export function deltaClass(c: Candidate): string {
  if (c.delta !== null) return c.delta > 0 ? 'increase' : c.delta < 0 ? 'decrease' : 'unchanged';
  if (c.delta_min !== null && c.delta_min > 0) return 'increase';
  if (c.delta_max !== null && c.delta_max < 0) return 'decrease';
  return 'unknown';
}
export function tokenText(vocab: Record<string, string>, id: number): string {
  const raw = vocab[String(id)];
  return raw === undefined ? `[token ${id}: text unavailable]` : raw.replaceAll(' ', '␠').replaceAll('\n', '↵').replaceAll('\t', '⇥') || '∅';
}
export function armLabel(arm: Arm): string {
  if (arm.name === 'base_repeat') return 'Baseline repeat';
  if (arm.kind === 'zero') return 'Armed zero';
  const names: Record<string,string> = { joyful:'Joy', calm:'Calm', sad:'Sadness [sad]', afraid:'Fear [afraid]', angry:'Anger [angry]', random1:'Random control' };
  const label=names[arm.kind ?? ''] ?? arm.kind ?? arm.name;
  const dose=arm.s===null ? '—' : `${arm.s>0?'+':''}${arm.s}`;
  return `${label} · ${dose}${arm.s!==null && arm.s<0?' · counter-steered':''}`;
}
export function jsonEqual(a: unknown, b: unknown): boolean {
  const canonical=(x: unknown): unknown => Array.isArray(x) ? x.map(canonical) : x && typeof x==='object' ? Object.fromEntries(Object.entries(x).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,canonical(v)])) : x;
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}
export function relatedScenarios(index: BundleIndex, scenario: Scenario | null): Scenario[] {
  if (!scenario?.source) return [];
  return Object.values(index.scenarios ?? {}).filter(s=>s.key!==scenario.key && s.source?.sha256===scenario.source!.sha256);
}
export function channelJumps(positions: Position[]): { reasoning: number | null; answer: number | null } {
  // Navigate supplied labels only, never offsets guessed from a scenario's name or reference metadata.
  const first=(channel:string)=>positions.find(p=>p.source==='generated' && p.channel===channel)?.pos ?? null;
  return { reasoning:first('reasoning'), answer:first('answer') };
}

/** Presentation of declared boundaries only; never infer the prefill from a title. */
export function freePrefill(s:Scenario|null|undefined):'prompt'|'recorded-thinking'|'unspecified'{
  if(!s?.source)return 'unspecified';
  if(s.P===s.source.prompt_len)return 'prompt';
  if(s.P===s.source.answer_start)return 'recorded-thinking';
  return 'unspecified';
}
