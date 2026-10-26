/**
 * Test support: a fake Nomos serve over node:http that speaks Plan 2's SSE (pyhost/serve.py `_sse`). It refuses a bad
 * `nomos` block with Plan 2's own 400 (`_resolve_nomos`; ruling R1), builds its records from the request's settings, and
 * can break a reply in the ways the persistence lifecycle must survive.
 */
import http from "node:http";
import { READOUT_V, type ReadoutRecord } from "../src/contract.ts";
import { BACKGROUND_CONTEXT } from "../src/pi.ts";

export const ctx = BACKGROUND_CONTEXT;
/** A projection value no real reply carries: model content must never hold it (ruling R10, finding #1.3). */
export const SENTINEL = 271.828;
/** The fake direction set's names (E = 2). */
export const NAMES = ["calm", "joyful"];

export type ServeStep = {
	status?: number;
	body?: unknown;
	chunks?: unknown[];
	delayMs?: number;
	/** "done" (the default) ends with [DONE]; "cut" ends the body with no finish chunk; "hold" keeps it open. */
	end?: "done" | "cut" | "hold";
};
export type Handler = (body: any, index: number) => ServeStep;
export type FakeServe = { url: string; bodies: any[]; refused: string[]; aborted(): number; close(): Promise<void> };

const keys = (o: object): string => JSON.stringify(Object.keys(o).sort());

/** The 400 detail Plan 2's `_resolve_nomos` gives a block, for every check that needs no asset registry. */
export function nomosFault(nomos: unknown): string | undefined {
	if (nomos === undefined || nomos === null) return undefined;
	if (typeof nomos !== "object" || Array.isArray(nomos)) return "must be an object";
	const n = nomos as Record<string, any>;
	const unknown = Object.keys(n).filter((k) => !["readout", "steer", "conversation", "turn"].includes(k));
	if (unknown.length > 0) return `unknown fields ${JSON.stringify(unknown.sort())}`;
	const { conversation, turn, readout, steer } = n;
	if (conversation !== undefined && conversation !== null && (typeof conversation !== "string" || conversation.length > 256)) {
		return "conversation must be a string of at most 256 characters";
	}
	if (turn !== undefined && turn !== null && (!Number.isSafeInteger(turn) || turn < 0)) return "turn must be a non-negative integer";
	if (readout !== undefined && readout !== null) {
		const fields = typeof readout === "object" ? Object.keys(readout) : [];
		if (typeof readout !== "object" || fields.some((k) => !["emotion", "workspace", "prompt"].includes(k)) || !fields.includes("emotion")) {
			return "readout must be an object with emotion and, optionally, workspace and prompt";
		}
		if (typeof readout.emotion !== "object" || readout.emotion === null || keys(readout.emotion) !== keys({ directions: 0, layers: 0 })) {
			return "readout.emotion must be an object with exactly ['directions', 'layers']";
		}
		const ws = readout.workspace;
		if (ws !== undefined && ws !== null) {
			if (typeof ws !== "object" || keys(ws) !== keys({ k: 0, layers: 0, lens: 0 })) {
				return "readout.workspace must be an object with exactly ['k', 'layers', 'lens']";
			}
			if (!Number.isSafeInteger(ws.k) || ws.k < 1 || ws.k > 16) return "readout.workspace.k must be an integer in [1, 16]";
		}
		if (readout.prompt !== undefined && readout.prompt !== "new" && readout.prompt !== "all") return 'readout.prompt must be "new" or "all"';
	}
	if (steer !== undefined && steer !== null) {
		if (typeof steer !== "object" || keys(steer) !== keys({ direction: 0, directions: 0, layer: 0, strength: 0 })) {
			return "steer must be an object with exactly ['direction', 'directions', 'layer', 'strength']";
		}
		if (typeof steer.strength !== "number" || !Number.isFinite(steer.strength) || Math.abs(steer.strength) > 2) {
			return "steer.strength must be a finite number in [-2.0, 2.0] (ruling P2-R7)";
		}
	}
	return undefined;
}

export async function fakeServe(handler: Handler): Promise<FakeServe> {
	const bodies: any[] = [];
	const refused: string[] = [];
	let aborted = 0;
	const server = http.createServer(async (req, res) => {
		let raw = "";
		for await (const part of req) raw += part;
		const body = JSON.parse(raw);
		bodies.push(body);
		const fault = nomosFault(body.nomos);
		if (fault !== undefined) refused.push(`nomos: ${fault}`);
		const step: ServeStep = fault === undefined ? handler(body, bodies.length - 1) : { status: 400, body: { detail: `nomos: ${fault}` } };
		if (step.status !== undefined && step.status !== 200) {
			res.writeHead(step.status, { "content-type": "application/json" });
			res.end(JSON.stringify(step.body ?? { detail: "error" }));
			return;
		}
		res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
		res.on("close", () => {
			if (!res.writableFinished) aborted += 1;
		});
		for (const c of step.chunks ?? []) {
			if (res.destroyed) return;
			res.write(`data: ${JSON.stringify(c)}\n\n`);
			if (step.delayMs) await new Promise((r) => setTimeout(r, step.delayMs));
		}
		if (res.destroyed || step.end === "hold") return;
		if (step.end === "cut") res.end();
		else res.end("data: [DONE]\n\n");
	});
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
	const { port } = server.address() as { port: number };
	return {
		url: `http://127.0.0.1:${port}/v1`,
		bodies,
		refused,
		aborted: () => aborted,
		close: () =>
			new Promise<void>((resolve) => {
				server.closeAllConnections();
				server.close(() => resolve());
			}),
	};
}

/** One chat.completion.chunk as the serve writes it, with an optional `nomos` object. */
export function chunk(id: string, delta: object, finish: string | null = null, nomos?: object): object {
	const c: Record<string, unknown> = {
		id,
		object: "chat.completion.chunk",
		created: 1,
		model: "nomos-muse-glimmer-30b",
		choices: [{ index: 0, delta, finish_reason: finish }],
	};
	if (nomos !== undefined) c.nomos = nomos;
	return c;
}

/** A request's record dimensions: emotion layers and names, and the workspace's layers and k. */
export type Shape = { layers: number; names: number; workspace: { layers: number; k: number } | null };

/** A record the way Plan 2 assembles one: emotion on every record, the workspace on generated ones only. */
export function record(pos: number, source: "prompt" | "generated", shape: Shape): ReadoutRecord {
	const generated = source === "generated";
	const ws = generated ? shape.workspace : null;
	return {
		pos,
		token: 1000 + pos,
		source,
		channel: generated ? "answer" : null,
		emotion: {
			proj: Array.from({ length: shape.layers }, () => Array.from({ length: shape.names }, (_, e) => (e === 0 ? SENTINEL : -1.25))),
			norm: Array.from({ length: shape.layers }, () => 100 + pos),
		},
		workspace:
			ws === null
				? null
				: { top: Array.from({ length: ws.layers }, () => Array.from({ length: ws.k }, (_, i): [number, number] => [500 + i, 9.5 - i])) },
		missing: generated ? [] : shape.workspace === null ? ["channel_prompt_v1"] : ["workspace_prompt_v1", "channel_prompt_v1"],
	};
}

/**
 * How a reply goes wrong:
 * - "no_summary" ends normally without the summary;
 * - "cut" ends the body after the records with no finish chunk (a transport failure); "hold" keeps it open there;
 * - "summary_cut" and "summary_hold" send the summary on a chunk without finish_reason, then cut or hold the body;
 * - "late_nomos" sends one more readout object after the finish chunk's summary;
 * - "v2" names another contract version; "bad_record" sends a valid-version record with one emotion row for three layers;
 * - "bad_counts" miscounts the summary's records, and "kv_reused" its reused positions;
 * - "role_id" gives the opening role chunk another completion ID.
 */
export type Fault =
	| "no_summary"
	| "cut"
	| "hold"
	| "summary_cut"
	| "summary_hold"
	| "late_nomos"
	| "v2"
	| "bad_record"
	| "bad_counts"
	| "kv_reused"
	| "role_id";

/**
 * Replies like Plan 2's serve:
 * - without a `nomos` block, a plain answer;
 * - with a block that has no readouts, a plain answer whose finish chunk carries Plan 2's off-mode summary (its P2-R13;
 *   ruling R2): empty ranges, zero counts, and `readout_off: 1`;
 * - with readouts, records ride the content chunks (the header on the first, the summary on the finish chunk).
 * Prompt records cover [0, promptLen) under "prompt": "all", otherwise only the last prompt position (as after a KV-store
 * reuse). Generated records cover every answer token. `faults` breaks the reply of request `index`.
 */
export function readoutHandler(answerLen = 6, promptLen = 4, faults: Record<number, Fault> = {}): Handler {
	return (body, index) => {
		const id = `chatcmpl-${index + 1}`;
		const fault = faults[index];
		const n = body.nomos;
		const chunks: object[] = [chunk(fault === "role_id" ? `${id}-role` : id, { role: "assistant" }), chunk(id, { reasoning_content: "thinking " })];
		if (n === undefined || n === null) {
			for (let i = 0; i < answerLen; i++) chunks.push(chunk(id, { content: `w${i} ` }));
			chunks.push(chunk(id, {}, "stop"));
			return { chunks };
		}
		const r = n.readout ?? null;
		const header = {
			v: fault === "v2" ? "nomos.readout/2" : READOUT_V,
			conversation: n.conversation ?? null,
			turn: n.turn ?? null,
			response_id: id,
			settings: r === null ? null : { emotion: { ...r.emotion, names: NAMES }, workspace: r.workspace ?? null, prompt: r.prompt },
			steer: n.steer ? { ...n.steer, from_pos: promptLen - 1 } : null,
		};
		if (r === null) {
			for (let i = 0; i < answerLen; i++) chunks.push(chunk(id, { content: `w${i} ` }));
			const off = {
				header,
				ranges: { reused: [0, 0], prompt: [0, 0], generated: [0, 0] },
				counts: { records: 0, emotion_valid: 0, workspace_valid: 0 },
				missing: { kv_reused: 0, natural_stop: 1, settle_trimmed: 0, readout_off: 1 },
			};
			chunks.push(chunk(id, {}, "stop", { summary: off }));
			return { chunks };
		}
		const shape: Shape = {
			layers: r.emotion.layers.length,
			names: NAMES.length,
			workspace: r.workspace ? { layers: r.workspace.layers.length, k: r.workspace.k } : null,
		};
		const first = r.prompt === "all" ? 0 : promptLen - 1;
		let pending: ReadoutRecord[] = [];
		for (let p = first; p < promptLen; p++) pending.push(record(p, "prompt", shape));
		let sentHeader = false;
		for (let i = 0; i < answerLen; i++) {
			if (i > 0) pending.push(record(promptLen + i - 1, "generated", shape));
			if (fault === "bad_record" && i === 2) pending[0] = { ...pending[0], emotion: { proj: [[1, 1]], norm: [1] } };
			const nomos = pending.length ? { ...(sentHeader ? {} : { header }), readout: pending } : undefined;
			if (nomos) sentHeader = true;
			chunks.push(chunk(id, { content: `w${i} ` }, null, nomos));
			pending = [];
		}
		if (fault === "cut" || fault === "hold") return { chunks, end: fault };
		pending.push(record(promptLen + answerLen - 1, "generated", shape));
		const total = promptLen - first + answerLen;
		const summary = {
			header,
			ranges: { reused: [0, first], prompt: [first, promptLen], generated: [promptLen, promptLen + answerLen] },
			counts: { records: fault === "bad_counts" ? total + 1 : total, emotion_valid: total, workspace_valid: shape.workspace === null ? 0 : answerLen },
			missing: { kv_reused: fault === "kv_reused" ? first + 1 : first, natural_stop: 1, settle_trimmed: 0 },
		};
		if (fault === "summary_cut" || fault === "summary_hold") {
			chunks.push(chunk(id, {}, null, { readout: pending, summary }));
			return { chunks, end: fault === "summary_cut" ? "cut" : "hold" };
		}
		chunks.push(chunk(id, {}, "stop", fault === "no_summary" ? { readout: pending } : { readout: pending, summary }));
		if (fault === "late_nomos") chunks.push({ id, object: "chat.completion.chunk", created: 1, model: "nomos-muse-glimmer-30b", choices: [], nomos: { readout: [] } });
		return { chunks };
	};
}

/** `promise`, or a rejection that names `what` after `ms` milliseconds. */
export async function within<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	const late = new Promise<never>((_, reject) => {
		timer = setTimeout(() => reject(new Error(`timed out after ${ms} ms: ${what}`)), ms);
	});
	try {
		return await Promise.race([promise, late]);
	} finally {
		clearTimeout(timer);
	}
}

/** Polls `cond`, which may be async, every 10 ms. */
export async function waitFor(cond: () => boolean | Promise<boolean>, timeoutMs = 5000): Promise<void> {
	const end = Date.now() + timeoutMs;
	while (!(await cond())) {
		if (Date.now() > end) throw new Error("timed out waiting");
		await new Promise((r) => setTimeout(r, 10));
	}
}
