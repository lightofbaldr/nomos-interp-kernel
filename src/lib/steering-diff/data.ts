import { parseBundle, parseIndex } from './model';
import type { BundleIndex, Catalog, ComparisonRef, Dataset } from './model';
export async function checkedJSON(url: string, sha: string, signal?: AbortSignal): Promise<unknown> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Cannot load ${url}: HTTP ${res.status}`);
  const bytes = await res.arrayBuffer();
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2,'0')).join('');
  if (hash !== sha) throw new Error('Bundle integrity check failed; no comparison displayed.');
  return JSON.parse(new TextDecoder().decode(bytes));
}
export async function catalog(): Promise<Catalog> {
  const r = await fetch('/data/catalog.json');
  if (!r.ok) throw new Error('Local recorded bundles are unavailable. Configure GLIMMER_STEERDIFF_DATA_DIR on the server; no data is bundled into this app.');
  const c = await r.json();
  if (c.schema !== 'glimmer.local-catalog/1' || !c.datasets?.length) throw new Error('Invalid local catalog');
  return c;
}
export async function loadIndex(d: Dataset) { return parseIndex(await checkedJSON(`/data/${d.id}/index.json`, d.hashes['index.json'])); }
export async function loadBundle(d: Dataset, idx: BundleIndex, ref: ComparisonRef, signal?: AbortSignal) {
  return parseBundle(await checkedJSON(`/data/${d.id}/${ref.path}`, d.hashes[ref.path], signal), idx, ref);
}
