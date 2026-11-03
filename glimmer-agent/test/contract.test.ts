import assert from "node:assert/strict";
import { test } from "node:test";
import { ContractError, type NomosBlock, READOUT_V, type ReadoutHeader, type ReadoutSettings, TurnCheck } from "../src/contract.ts";

const ID = "chatcmpl-9";
const DIRS = "d".repeat(64);
const SETTINGS: ReadoutSettings = { emotion: { layers: [20, 24], directions: DIRS, names: ["calm", "joyful"] }, workspace: null, prompt: "all" };
const BLOCK: NomosBlock = { readout: { emotion: { layers: [20, 24], directions: DIRS }, prompt: "all" }, conversation: "7", turn: 3 };
const HEADER: ReadoutHeader = { v: READOUT_V, conversation: "7", turn: 3, response_id: ID, settings: SETTINGS, steer: null };

/** A record as the serve sends it: 2 layers by 2 directions. */
function rec(pos: number, source: "prompt" | "generated", over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		pos,
		token: 1000 + pos,
		source,
		channel: source === "prompt" ? null : "answer",
		emotion: { proj: [[0.5, -1], [0.25, 2]], norm: [10, 11] },
		workspace: null,
		missing: source === "prompt" ? ["channel_prompt_v1"] : [],
		...over,
	};
}

/** The summary of a turn with prompt records [0, 2) and generated records [2, 4). */
function summary(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		header: HEADER,
		ranges: { reused: [0, 0], prompt: [0, 2], generated: [2, 4] },
		counts: { records: 4, emotion_valid: 4, workspace_valid: 0 },
		missing: { kv_reused: 0, natural_stop: 1, settle_trimmed: 0 },
		...over,
	};
}

/** One SSE chunk of the completion. */
function sse(nomos: unknown, id = ID): object {
	return { id, object: "chat.completion.chunk", choices: [], nomos };
}

const OPEN = sse({ header: HEADER, readout: [rec(0, "prompt"), rec(1, "prompt")] });

/** Feed chunks to a fresh check: the message of the first ContractError, or undefined. */
function firstError(block: NomosBlock, chunks: object[]): string | undefined {
	const check = new TurnCheck(block);
	try {
		for (const c of chunks) check.take(c);
	} catch (error) {
		assert.ok(error instanceof ContractError, `a ContractError, not ${String(error)}`);
		return error.message;
	}
	return undefined;
}

test("contract: a whole turn checks out, and the checked records are fresh copies", () => {
	const check = new TurnCheck(BLOCK);
	assert.equal(check.take({ id: ID, choices: [] }), undefined, "a chunk without nomos has none");
	const first = rec(0, "prompt");
	const opened = check.take(sse({ header: HEADER, readout: [first, rec(1, "prompt")] }));
	assert.deepEqual(opened?.header, HEADER);
	assert.deepEqual(opened?.records.map((r) => r.pos), [0, 1]);
	assert.notEqual(opened?.records[0], first, "a fresh copy, not the parsed object");
	assert.deepEqual(check.take(sse({ readout: [rec(2, "generated")] }))?.records.map((r) => r.channel), ["answer"]);
	const last = check.take(sse({ readout: [rec(3, "generated")], summary: summary() }));
	assert.deepEqual(last?.records.map((r) => r.pos), [3]);
	assert.deepEqual(last?.summary, summary());
});

test("contract: another contract version, or a nomos object that is not one, is a ContractError", () => {
	assert.match(firstError(BLOCK, [sse({ header: { ...HEADER, v: "nomos.readout/2" } })]) ?? "", /nomos\.readout\/2/);
	assert.match(firstError(BLOCK, [sse("x")]) ?? "", /nomos is not an object/);
	assert.match(firstError(BLOCK, [sse({ header: HEADER, readout: {} })]) ?? "", /readout is not a list/);
	assert.match(firstError(BLOCK, [sse({ header: HEADER, extra: 1 })]) ?? "", /unknown field extra/);
	assert.match(firstError(BLOCK, [sse({ summary: summary({ header: { ...HEADER, v: "other" } }) })]) ?? "", /contract other/);
});

test("contract: records follow the header in position order, prompt records first, without gaps", () => {
	assert.match(firstError(BLOCK, [sse({ readout: [rec(0, "prompt")] })]) ?? "", /records before the header/);
	assert.match(firstError(BLOCK, [sse({ header: HEADER, readout: [rec(1, "prompt"), rec(0, "prompt")] })]) ?? "", /a record at 0 after one at 1/);
	assert.match(firstError(BLOCK, [sse({ header: HEADER, readout: [rec(0, "prompt"), rec(2, "prompt")] })]) ?? "", /prompt records skip from 0 to 2/);
	assert.match(firstError(BLOCK, [OPEN, sse({ readout: [rec(2, "generated"), rec(4, "generated")] })]) ?? "", /generated records skip from 2 to 4/);
	assert.match(firstError(BLOCK, [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "prompt")] })]) ?? "", /a prompt record at 3 after a generated record/);
	assert.match(firstError(BLOCK, [OPEN, sse({ header: HEADER })]) ?? "", /a second header/);
});

test("contract: a record holds finite numbers in the header's dimensions, a channel for generated tokens only, and reasons for what it lacks", () => {
	const bad = (r: unknown): string => firstError(BLOCK, [sse({ header: HEADER, readout: [r] })]) ?? "";
	assert.match(bad(42), /readout\[0\] is not an object/);
	assert.match(bad(rec(0, "prompt", { pos: -1 })), /pos is not a non-negative integer/);
	assert.match(bad(rec(0, "prompt", { emotion: { proj: [[0.5, -1]], norm: [10] } })), /emotion has 1 rows and 1 norms for 2 layers/);
	assert.match(bad(rec(0, "prompt", { emotion: { proj: [[0.5], [0.25]], norm: [10, 11] } })), /proj\[0\] has 1 values for 2 directions/);
	assert.match(bad(rec(0, "prompt", { emotion: { proj: [[0.5, Number.NaN], [0.25, 2]], norm: [10, 11] } })), /proj\[0\]\[1\] is not a finite number/);
	assert.match(bad(rec(0, "prompt", { emotion: { proj: [[0.5, -1], [0.25, 2]], norm: [10, -1] } })), /norm\[1\] is negative/);
	assert.match(bad(rec(0, "generated", { channel: null })), /channel is null/);
	assert.match(bad(rec(0, "prompt", { channel: "answer" })), /a prompt record has channel answer/);
	assert.match(bad(rec(0, "prompt", { emotion: null, missing: [] })), /no emotion readout and names no reason/);
	assert.match(bad(rec(0, "generated", { workspace: { top: [] } })), /a workspace readout where none is computed/);
});

test("contract: a workspace turn carries k scored tokens per workspace layer on generated records", () => {
	const workspace = { layers: [36, 40], lens: "e".repeat(64), k: 2 };
	const block: NomosBlock = { ...BLOCK, readout: { emotion: { layers: [20, 24], directions: DIRS }, workspace, prompt: "all" } };
	const header: ReadoutHeader = { ...HEADER, settings: { ...SETTINGS, workspace } };
	const reasons = { missing: ["workspace_prompt_v1", "channel_prompt_v1"] };
	const open = sse({ header, readout: [rec(0, "prompt", reasons), rec(1, "prompt", reasons)] });
	const top = [[[5, 1.5], [6, 0.5]], [[7, 2], [8, 1]]];
	const generated = (over: Record<string, unknown>): object => sse({ readout: [rec(2, "generated", over)] });
	assert.equal(firstError(block, [open, generated({ workspace: { top } })]), undefined);
	assert.match(firstError(block, [open, generated({ workspace: { top: [top[0]] } })]) ?? "", /top has 1 rows for 2 layers/);
	assert.match(firstError(block, [open, generated({ workspace: { top: [[[5, 1.5]], [[7, 2]]] } })]) ?? "", /top\[0\] has 1 pairs, k is 2/);
	assert.match(firstError(block, [open, generated({ workspace: null })]) ?? "", /no workspace readout and names no reason/);
	assert.match(firstError(BLOCK, [sse({ header })]) ?? "", /header\.settings\.workspace is/);
});

test("contract: the header and the summary agree with the request and the completion", () => {
	const opened = (over: Record<string, unknown>): string => firstError(BLOCK, [sse({ header: { ...HEADER, ...over } })]) ?? "";
	assert.match(opened({ conversation: 7 }), /conversation is not a string/);
	assert.match(opened({ turn: 4 }), /header\.turn is 4, expected 3/);
	assert.match(opened({ settings: { ...SETTINGS, prompt: "new" } }), /prompt is new, expected all/);
	assert.match(opened({ settings: { ...SETTINGS, emotion: { layers: [20], directions: DIRS, names: ["calm"] } } }), /header\.settings\.emotion is/);
	assert.match(opened({ steer: { layer: 24, directions: DIRS, direction: "calm", strength: 0.7, from_pos: 3 } }), /steer is set for a request without steering/);
	assert.match(firstError(BLOCK, [sse({ header: HEADER }, "chatcmpl-other")]) ?? "", /not the completion ID chatcmpl-other/);
	assert.match(firstError(BLOCK, [OPEN, sse({ readout: [] }, "chatcmpl-other")]) ?? "", /a chunk of completion chatcmpl-other/);
	const other = summary({ header: { ...HEADER, response_id: "chatcmpl-x" } });
	assert.match(firstError(BLOCK, [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "generated")], summary: other })]) ?? "", /summary\.header is/);
});

test("contract: the summary's counts and ranges match the records it closes", () => {
	const closing = (s: Record<string, unknown>): string =>
		firstError(BLOCK, [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "generated")], summary: s })]) ?? "";
	assert.match(closing(summary({ counts: { records: 5, emotion_valid: 4, workspace_valid: 0 } })), /summary\.counts is/);
	assert.match(closing(summary({ ranges: { reused: [0, 0], prompt: [0, 2], generated: [2, 5] } })), /generated records cover \[2,4\], the summary says \[2,5\]/);
	assert.match(closing(summary({ ranges: { reused: [0, 1], prompt: [0, 2], generated: [2, 4] } })), /not contiguous from 0/);
	assert.match(closing(summary({ missing: { kv_reused: 0, natural_stop: 1, settle_trimmed: 0, readout_off: 1 } })), /summary\.missing's reasons is/);
	assert.match(closing(summary({ missing: { kv_reused: 0, natural_stop: 2, settle_trimmed: 0 } })), /natural_stop is 2, not 0 or 1/);
	assert.match(closing(summary({ missing: { kv_reused: 999, natural_stop: 1, settle_trimmed: 0 } })), /kv_reused is 999, the reused range holds 0/);
	const twice = [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "generated")], summary: summary() }), sse({ readout: [] })];
	assert.match(firstError(BLOCK, twice) ?? "", /after the summary/);
});

test("contract: every chunk carries the pinned completion ID, ordinary ones too, and close() needs that completion's summary", () => {
	const role = { id: "chatcmpl-first-role", object: "chat.completion.chunk", choices: [] };
	assert.match(firstError(BLOCK, [role, OPEN]) ?? "", /a chunk of completion chatcmpl-9 in completion chatcmpl-first-role/);
	assert.match(firstError(BLOCK, [OPEN, { id: "chatcmpl-other", choices: [] }]) ?? "", /a chunk of completion chatcmpl-other in completion chatcmpl-9/);
	for (const id of [undefined, null, 42, ""]) {
		const ordinary = id === undefined ? { choices: [] } : { id, choices: [] };
		assert.match(firstError(BLOCK, [OPEN, ordinary]) ?? "", /a chunk without a completion ID/, `an ordinary chunk with id ${String(id)}`);
	}
	const check = new TurnCheck(BLOCK);
	check.take({ id: ID, choices: [] });
	check.take(OPEN);
	assert.throws(() => check.close(ID), /the reply ended without a readout summary/);
	check.take(sse({ readout: [rec(2, "generated"), rec(3, "generated")], summary: summary() }));
	assert.throws(() => check.close("chatcmpl-first-role"), /completion ID chatcmpl-first-role is not the readout's chatcmpl-9/);
	assert.deepEqual(check.close(ID), summary());
});

test("contract: a request without readouts takes Plan 2's off-mode summary exactly (P2-R13, ruling R2)", () => {
	const steer = { layer: 24, directions: DIRS, direction: "calm", strength: 0.7 };
	const block: NomosBlock = { steer, conversation: "17", turn: 4 };
	const off = JSON.parse(`{"header": {"v": "nomos.readout/1", "conversation": "17", "turn": 4, "response_id": "${ID}", "settings": null,
		"steer": {"layer": 24, "directions": "${DIRS}", "direction": "calm", "strength": 0.7, "from_pos": 7}},
		"ranges": {"reused": [0, 0], "prompt": [0, 0], "generated": [0, 0]},
		"counts": {"records": 0, "emotion_valid": 0, "workspace_valid": 0},
		"missing": {"kv_reused": 0, "natural_stop": 1, "settle_trimmed": 0, "readout_off": 1}}`);
	assert.deepEqual(new TurnCheck(block).take(sse({ summary: off })), { header: null, records: [], summary: off });
	const bare = { ...off, header: { ...off.header, steer: null }, missing: { ...off.missing, natural_stop: 0 } };
	assert.equal(firstError({ conversation: "17", turn: 4 }, [sse({ summary: bare })]), undefined, "a metadata-only block, no stop");
	assert.match(firstError(block, [sse({ summary: { ...off, missing: { kv_reused: 0, natural_stop: 1, settle_trimmed: 0 } } })]) ?? "", /summary\.missing's reasons is/);
	assert.match(firstError(block, [sse({ summary: { ...off, missing: { ...off.missing, readout_off: 2 } } })]) ?? "", /summary\.missing is/);
	assert.match(firstError(block, [sse({ summary: { ...off, ranges: { reused: [0, 0], prompt: [0, 8], generated: [8, 14] } } })]) ?? "", /summary\.ranges is/);
	assert.match(firstError(block, [sse({ header: off.header, readout: [rec(0, "prompt")] })]) ?? "", /a record for a request without readouts/);
	assert.match(firstError(block, [sse({ summary: { ...off, header: { ...off.header, settings: SETTINGS } } })]) ?? "", /settings are set for a request without readouts/);
});

test("contract: R5: a record outside the summary's ranges breaks the contract wherever it arrived; an unfinished turn keeps what it received", () => {
	const inSummaryChunk = [OPEN, sse({ readout: [rec(2, "generated")] }), sse({ readout: [rec(3, "generated"), rec(4, "generated")], summary: summary() })];
	assert.match(firstError(BLOCK, inSummaryChunk) ?? "", /records outside the summary's ranges \(ruling R5\)/);
	const earlier = [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "generated"), rec(4, "generated")] }), sse({ readout: [], summary: summary() })];
	assert.match(firstError(BLOCK, earlier) ?? "", /records outside the summary's ranges \(ruling R5\)/);
	const check = new TurnCheck(BLOCK);
	const kept = [OPEN, sse({ readout: [rec(2, "generated"), rec(3, "generated"), rec(4, "generated")] })].flatMap((c) => check.take(c)?.records ?? []);
	assert.deepEqual(kept.map((r) => r.pos), [0, 1, 2, 3, 4], "no summary: every received record stays");
});
