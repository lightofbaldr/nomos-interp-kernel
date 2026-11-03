/**
 * The readout record contract, nomos.readout/1 (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md,
 * section 1), as the serve sends it (readout-spine Plan 2), and its runtime check (review ruling R10, finding #14).
 * - The stored shapes have no optional fields, so they are JSON values that pi-durable entries can hold.
 * - TurnCheck checks one request's SSE chunks, in order, against the request's block: the shapes, finite numbers, the
 *   header's dimensions, position order, and the agreement of the header, the summary, the request and the completion
 *   ID. It returns fresh copies, never the parsed objects, and throws ContractError at the first violation.
 * - A complete turn's records lie inside its summary's ranges, which they cover exactly: Plan 2's settlement keeps only
 *   delivered positions, so a record outside them breaks the contract, wherever it arrived (ruling R5).
 * - Every chunk of the stream, ordinary ones too, carries the completion ID the first one pinned (review F5): a
 *   non-empty string, so a chunk whose `id` is absent, null or a number breaks the contract too.
 * - A summary is only a candidate until the stream ends successfully: close() hands it out for the completion the stock
 *   message names (review F1).
 */
import { isDeepStrictEqual } from "node:util";

export const READOUT_V = "nomos.readout/1";

export type Channel = "reasoning" | "answer" | "template" | "tool";

export type ReadoutSettings = {
	emotion: { layers: number[]; directions: string; names: string[] };
	workspace: { layers: number[]; lens: string; k: number } | null;
	prompt: "new" | "all";
};

export type SteerSettings = { layer: number; directions: string; direction: string; strength: number; from_pos: number };

export type ReadoutHeader = {
	v: string;
	conversation: string | null;
	turn: number | null;
	response_id: string;
	settings: ReadoutSettings | null;
	steer: SteerSettings | null;
};

export type ReadoutRecord = {
	pos: number;
	token: number;
	source: "prompt" | "generated";
	channel: Channel | null;
	emotion: { proj: number[][]; norm: number[] } | null;
	workspace: { top: [number, number][][] } | null;
	missing: string[];
};

export type ReadoutSummary = {
	header: ReadoutHeader;
	ranges: { reused: [number, number]; prompt: [number, number]; generated: [number, number] };
	counts: { records: number; emotion_valid: number; workspace_valid: number };
	missing: Record<string, number>;
};

/** The request block the serve reads (spec section 3). `conversation` is `String(ConversationId)` (ruling R1). */
export type NomosBlock = {
	readout?: {
		emotion: { layers: number[]; directions: string };
		workspace?: { layers: number[]; lens: string; k: number };
		prompt: "new" | "all";
	};
	steer?: { layer: number; directions: string; direction: string; strength: number };
	conversation?: string;
	turn?: number;
};

/** One chunk's checked readouts: the header when it arrives, the new records, and the summary on the last chunk. */
export type TurnUpdate = { header: ReadoutHeader | null; records: ReadoutRecord[]; summary: ReadoutSummary | null };

/** A serve that broke nomos.readout/1. Its turn ends as contract_error; the reply itself goes on. */
export class ContractError extends Error {
	constructor(message: string) {
		super(`nomos: ${message}`);
		this.name = "ContractError";
	}
}

type Obj = Record<string, unknown>;

function fail(message: string): never {
	throw new ContractError(message);
}

function object(v: unknown, what: string): Obj {
	if (typeof v !== "object" || v === null || Array.isArray(v)) fail(`${what} is not an object`);
	return v as Obj;
}

function list(v: unknown, what: string): unknown[] {
	if (!Array.isArray(v)) fail(`${what} is not a list`);
	return v;
}

function text(v: unknown, what: string): string {
	if (typeof v !== "string") fail(`${what} is not a string`);
	return v;
}

function index(v: unknown, what: string): number {
	if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) fail(`${what} is not a non-negative integer`);
	return v;
}

function real(v: unknown, what: string): number {
	if (typeof v !== "number" || !Number.isFinite(v)) fail(`${what} is not a finite number`);
	return v;
}

function indices(v: unknown, what: string): number[] {
	return list(v, what).map((x, i) => index(x, `${what}[${i}]`));
}

function span(v: unknown, what: string): [number, number] {
	const pair = list(v, what);
	if (pair.length !== 2) fail(`${what} is not a [start, end) pair`);
	const start = index(pair[0], `${what}[0]`);
	const end = index(pair[1], `${what}[1]`);
	if (start > end) fail(`${what} ends before it starts`);
	return [start, end];
}

function agree(got: unknown, want: unknown, what: string): void {
	if (!isDeepStrictEqual(got, want)) fail(`${what} is ${JSON.stringify(got)}, expected ${JSON.stringify(want)}`);
}

function header(v: unknown, block: NomosBlock, what: string): ReadoutHeader {
	const h = object(v, what);
	if (h.v !== READOUT_V) fail(`${what}: contract ${String(h.v)}, expected ${READOUT_V}`);
	const conversation = h.conversation === null ? null : text(h.conversation, `${what}.conversation`);
	agree(conversation, block.conversation ?? null, `${what}.conversation`);
	const turn = h.turn === null ? null : index(h.turn, `${what}.turn`);
	agree(turn, block.turn ?? null, `${what}.turn`);
	const response_id = text(h.response_id, `${what}.response_id`);
	if (response_id === "") fail(`${what}.response_id is empty`);
	return {
		v: READOUT_V,
		conversation,
		turn,
		response_id,
		settings: settings(h.settings, block, `${what}.settings`),
		steer: steer(h.steer, block, `${what}.steer`),
	};
}

function settings(v: unknown, block: NomosBlock, what: string): ReadoutSettings | null {
	const asked = block.readout;
	if (asked === undefined) {
		if (v !== null) fail(`${what} are set for a request without readouts`);
		return null;
	}
	const s = object(v, what);
	const e = object(s.emotion, `${what}.emotion`);
	const layers = indices(e.layers, `${what}.emotion.layers`);
	const directions = text(e.directions, `${what}.emotion.directions`);
	agree({ layers, directions }, { layers: asked.emotion.layers, directions: asked.emotion.directions }, `${what}.emotion`);
	const names = list(e.names, `${what}.emotion.names`).map((x, i) => text(x, `${what}.emotion.names[${i}]`));
	if (names.length === 0) fail(`${what}.emotion.names is empty`);
	let workspace: ReadoutSettings["workspace"] = null;
	if (s.workspace !== null) {
		const w = object(s.workspace, `${what}.workspace`);
		workspace = { layers: indices(w.layers, `${what}.workspace.layers`), lens: text(w.lens, `${what}.workspace.lens`), k: index(w.k, `${what}.workspace.k`) };
	}
	const wanted = asked.workspace === undefined ? null : { layers: asked.workspace.layers, lens: asked.workspace.lens, k: asked.workspace.k };
	agree(workspace, wanted, `${what}.workspace`);
	if (s.prompt !== asked.prompt) fail(`${what}.prompt is ${String(s.prompt)}, expected ${asked.prompt}`);
	return { emotion: { layers, directions, names }, workspace, prompt: asked.prompt };
}

function steer(v: unknown, block: NomosBlock, what: string): SteerSettings | null {
	const asked = block.steer;
	if (asked === undefined) {
		if (v !== null) fail(`${what} is set for a request without steering`);
		return null;
	}
	const s = object(v, what);
	const got = {
		layer: index(s.layer, `${what}.layer`),
		directions: text(s.directions, `${what}.directions`),
		direction: text(s.direction, `${what}.direction`),
		strength: real(s.strength, `${what}.strength`),
	};
	agree(got, { layer: asked.layer, directions: asked.directions, direction: asked.direction, strength: asked.strength }, what);
	return { ...got, from_pos: index(s.from_pos, `${what}.from_pos`) };
}

function record(v: unknown, h: ReadoutHeader, what: string): ReadoutRecord {
	const s = h.settings;
	if (s === null) fail(`${what}: a record for a request without readouts`);
	const r = object(v, what);
	const pos = index(r.pos, `${what}.pos`);
	const token = index(r.token, `${what}.token`);
	const source = r.source;
	if (source !== "prompt" && source !== "generated") fail(`${what}.source is ${String(source)}`);
	const ch = r.channel;
	let channel: Channel | null = null;
	if (source === "generated") {
		if (ch !== "reasoning" && ch !== "answer" && ch !== "template" && ch !== "tool") fail(`${what}.channel is ${String(ch)}`);
		channel = ch;
	} else if (ch !== null) {
		fail(`${what}: a prompt record has channel ${String(ch)}`);
	}
	const layers = s.emotion.layers.length;
	const names = s.emotion.names.length;
	let emotion: ReadoutRecord["emotion"] = null;
	if (r.emotion !== null) {
		const e = object(r.emotion, `${what}.emotion`);
		const proj = list(e.proj, `${what}.emotion.proj`);
		const norm = list(e.norm, `${what}.emotion.norm`);
		if (proj.length !== layers || norm.length !== layers) {
			fail(`${what}.emotion has ${proj.length} rows and ${norm.length} norms for ${layers} layers`);
		}
		emotion = {
			proj: proj.map((row, l) => {
				const values = list(row, `${what}.emotion.proj[${l}]`);
				if (values.length !== names) fail(`${what}.emotion.proj[${l}] has ${values.length} values for ${names} directions`);
				return values.map((x, i) => real(x, `${what}.emotion.proj[${l}][${i}]`));
			}),
			norm: norm.map((x, l) => {
				const n = real(x, `${what}.emotion.norm[${l}]`);
				if (n < 0) fail(`${what}.emotion.norm[${l}] is negative`);
				return n;
			}),
		};
	}
	const ws = s.workspace;
	let workspace: ReadoutRecord["workspace"] = null;
	if (r.workspace !== null) {
		if (source === "prompt" || ws === null) fail(`${what} has a workspace readout where none is computed`);
		const top = list(object(r.workspace, `${what}.workspace`).top, `${what}.workspace.top`);
		if (top.length !== ws.layers.length) fail(`${what}.workspace.top has ${top.length} rows for ${ws.layers.length} layers`);
		workspace = {
			top: top.map((row, l) => {
				const pairs = list(row, `${what}.workspace.top[${l}]`);
				if (pairs.length !== ws.k) fail(`${what}.workspace.top[${l}] has ${pairs.length} pairs, k is ${ws.k}`);
				return pairs.map((p, i): [number, number] => {
					const pair = list(p, `${what}.workspace.top[${l}][${i}]`);
					if (pair.length !== 2) fail(`${what}.workspace.top[${l}][${i}] is not a [token, score] pair`);
					return [index(pair[0], `${what}.workspace.top[${l}][${i}][0]`), real(pair[1], `${what}.workspace.top[${l}][${i}][1]`)];
				});
			}),
		};
	}
	const missing = list(r.missing, `${what}.missing`).map((x, i) => text(x, `${what}.missing[${i}]`));
	if (emotion === null && missing.length === 0) fail(`${what} has no emotion readout and names no reason`);
	if (workspace === null && ws !== null && missing.length === 0) fail(`${what} has no workspace readout and names no reason`);
	return { pos, token, source, channel, emotion, workspace, missing };
}

/** Checks one request's `nomos` objects in arrival order (ruling R10, finding #14). */
export class TurnCheck {
	readonly #block: NomosBlock;
	readonly #counts = { records: 0, emotion_valid: 0, workspace_valid: 0 };
	#completion: string | null = null;
	#header: ReadoutHeader | null = null;
	#final: ReadoutSummary | null = null;
	#last = -1;
	#lastPrompt = -1;
	#lastGenerated = -1;
	#prompt: [number, number] | null = null;
	#generated: [number, number] | null = null;

	constructor(block: NomosBlock) {
		this.#block = block;
	}

	/** Check one parsed SSE chunk; undefined when it carries no `nomos`. */
	take(chunk: unknown): TurnUpdate | undefined {
		if (typeof chunk !== "object" || chunk === null) return undefined;
		const c = chunk as Obj;
		if (typeof c.id !== "string" || c.id === "") fail(`a chunk without a completion ID (id ${c.id === undefined ? "absent" : JSON.stringify(c.id)})`);
		if (this.#completion === null) this.#completion = c.id;
		else if (c.id !== this.#completion) fail(`a chunk of completion ${c.id} in completion ${this.#completion}`);
		if (!("nomos" in c)) return undefined;
		if (this.#final !== null) fail("a nomos object after the summary");
		const n = object(c.nomos, "nomos");
		for (const key of Object.keys(n)) {
			if (key !== "header" && key !== "readout" && key !== "summary") fail(`unknown field ${key}`);
		}
		let fresh: ReadoutHeader | null = null;
		if (n.header !== undefined) {
			if (this.#header !== null) fail("a second header");
			fresh = header(n.header, this.#block, "header");
			if (fresh.response_id !== this.#completion) {
				fail(`header.response_id ${fresh.response_id} is not the completion ID ${String(this.#completion)}`);
			}
			this.#header = fresh;
		}
		const turnHeader = this.#header;
		const raw = n.readout === undefined ? [] : list(n.readout, "readout");
		const records: ReadoutRecord[] = [];
		if (raw.length > 0) {
			if (turnHeader === null) fail("records before the header");
			for (const [i, v] of raw.entries()) records.push(this.#order(record(v, turnHeader, `readout[${i}]`)));
		}
		for (const r of records) this.#keep(r);
		if (n.summary === undefined) return { header: fresh, records, summary: null };
		const s = this.#summary(n.summary);
		this.#settle(s);
		this.#final = s;
		return { header: fresh, records, summary: s };
	}

	/**
	 * The summary of a stream that ended successfully (review F1, F5). Only a checked summary of the same completion makes
	 * a turn complete: a reply that ended without one, or whose final message names another completion, breaks the
	 * contract.
	 */
	close(responseId: string | undefined): ReadoutSummary {
		const summary = this.#final;
		if (summary === null) fail("the reply ended without a readout summary");
		if (responseId !== this.#completion) {
			fail(`the reply's completion ID ${String(responseId)} is not the readout's ${String(this.#completion)}`);
		}
		return summary;
	}

	/** Positions rise; prompt records come first, each kind without gaps. */
	#order(r: ReadoutRecord): ReadoutRecord {
		if (r.pos <= this.#last) fail(`a record at ${r.pos} after one at ${this.#last}`);
		if (r.source === "prompt") {
			if (this.#lastGenerated >= 0) fail(`a prompt record at ${r.pos} after a generated record`);
			if (this.#lastPrompt >= 0 && r.pos !== this.#lastPrompt + 1) fail(`prompt records skip from ${this.#lastPrompt} to ${r.pos}`);
			this.#lastPrompt = r.pos;
		} else {
			if (this.#lastGenerated >= 0 && r.pos !== this.#lastGenerated + 1) {
				fail(`generated records skip from ${this.#lastGenerated} to ${r.pos}`);
			}
			this.#lastGenerated = r.pos;
		}
		this.#last = r.pos;
		return r;
	}

	#keep(r: ReadoutRecord): void {
		const range = r.source === "prompt" ? this.#prompt : this.#generated;
		const next: [number, number] = range === null ? [r.pos, r.pos + 1] : [range[0], r.pos + 1];
		if (r.source === "prompt") this.#prompt = next;
		else this.#generated = next;
		this.#counts.records += 1;
		if (r.emotion !== null) this.#counts.emotion_valid += 1;
		if (r.workspace !== null) this.#counts.workspace_valid += 1;
	}

	#summary(v: unknown): ReadoutSummary {
		const s = object(v, "summary");
		const h = header(s.header, this.#block, "summary.header");
		if (this.#header !== null) agree(h, this.#header, "summary.header");
		else if (h.response_id !== this.#completion) {
			fail(`summary.header.response_id ${h.response_id} is not the completion ID ${String(this.#completion)}`);
		}
		const ranges = object(s.ranges, "summary.ranges");
		const reused = span(ranges.reused, "summary.ranges.reused");
		const prompt = span(ranges.prompt, "summary.ranges.prompt");
		const generated = span(ranges.generated, "summary.ranges.generated");
		if (reused[0] !== 0 || reused[1] !== prompt[0] || prompt[1] !== generated[0]) {
			fail(`summary.ranges ${JSON.stringify([reused, prompt, generated])} are not contiguous from 0`);
		}
		const counts = object(s.counts, "summary.counts");
		const missing: Record<string, number> = {};
		for (const [key, x] of Object.entries(object(s.missing, "summary.missing"))) missing[key] = index(x, `summary.missing.${key}`);
		return {
			header: h,
			ranges: { reused, prompt, generated },
			counts: {
				records: index(counts.records, "summary.counts.records"),
				emotion_valid: index(counts.emotion_valid, "summary.counts.emotion_valid"),
				workspace_valid: index(counts.workspace_valid, "summary.counts.workspace_valid"),
			},
			missing,
		};
	}

	/**
	 * The summary closes the turn.
	 * - A request without readouts gets Plan 2's off-mode summary (its P2-R13; ruling R2): empty ranges, zero counts, and
	 *   exactly kv_reused 0, natural_stop 0 or 1, settle_trimmed 0 and readout_off 1.
	 * - A recorded turn names exactly kv_reused, natural_stop and settle_trimmed. Its records lie inside the summary's
	 *   ranges and cover them exactly, and its counts are theirs (ruling R5).
	 */
	#settle(s: ReadoutSummary): void {
		const off = this.#block.readout === undefined;
		const reasons = off ? ["kv_reused", "natural_stop", "readout_off", "settle_trimmed"] : ["kv_reused", "natural_stop", "settle_trimmed"];
		agree(Object.keys(s.missing).sort(), reasons, "summary.missing's reasons");
		if (s.missing.natural_stop > 1) fail(`summary.missing.natural_stop is ${s.missing.natural_stop}, not 0 or 1`);
		const reused = s.ranges.reused[1] - s.ranges.reused[0];
		if (s.missing.kv_reused !== reused) fail(`summary.missing.kv_reused is ${s.missing.kv_reused}, the reused range holds ${reused}`);
		if (off) {
			agree(s.ranges, { reused: [0, 0], prompt: [0, 0], generated: [0, 0] }, "summary.ranges");
			agree(s.counts, { records: 0, emotion_valid: 0, workspace_valid: 0 }, "summary.counts");
			const { kv_reused, settle_trimmed, readout_off } = s.missing;
			agree({ kv_reused, settle_trimmed, readout_off }, { kv_reused: 0, settle_trimmed: 0, readout_off: 1 }, "summary.missing");
			return;
		}
		const outside = (got: [number, number] | null, range: [number, number]): boolean =>
			got !== null && (got[0] < range[0] || got[1] > range[1]);
		if (outside(this.#prompt, s.ranges.prompt) || outside(this.#generated, s.ranges.generated)) {
			const got = JSON.stringify({ prompt: this.#prompt, generated: this.#generated });
			fail(`records outside the summary's ranges (ruling R5): ${got}, ranges ${JSON.stringify(s.ranges)}`);
		}
		agree(s.counts, this.#counts, "summary.counts");
		const covers = (got: [number, number] | null, range: [number, number], what: string): void => {
			if (got === null ? range[0] !== range[1] : !isDeepStrictEqual(got, range)) {
				fail(`${what} records cover ${JSON.stringify(got)}, the summary says ${JSON.stringify(range)}`);
			}
		};
		covers(this.#prompt, s.ranges.prompt, "prompt");
		covers(this.#generated, s.ranges.generated, "generated");
	}
}
