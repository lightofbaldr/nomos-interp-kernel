import assert from "node:assert/strict";
import { test } from "node:test";
import { type AssistantMessage, createModels, createProvider, type Model, normalizeContext, type StreamOptions } from "@earendil-works/pi-ai";
import { stream as stockStream, streamSimple as stockSimple } from "@earendil-works/pi-ai/api/openai-completions";
import { type NomosBlock, TurnCheck } from "../src/contract.ts";
import { NOMOS_MODEL, NOMOS_PROVIDER, nomosModel, nomosProvider, type ReadoutSink, ReadoutRequests } from "../src/provider.ts";
import { chunk, fakeServe, readoutHandler, SENTINEL, waitFor } from "./support.ts";

const BLOCK: NomosBlock = { readout: { emotion: { layers: [20, 24, 29], directions: "d".repeat(64) }, prompt: "all" }, conversation: "7", turn: 0 };

function context() {
	return normalizeContext({ systemPrompt: "be brief", messages: [{ role: "user", content: "hello", timestamp: 1 }] });
}

type Run = { context?: ReturnType<typeof context>; sessionId?: string; signal?: AbortSignal; observer?: StreamOptions["onProviderStreamEvent"] };

async function run(models: ReturnType<typeof createModels>, model: Model<any>, options: Run = {}) {
	const events: string[] = [];
	const s = models.streamSimple(model, options.context ?? context(), {
		sessionId: options.sessionId,
		signal: options.signal,
		onProviderStreamEvent: options.observer,
	});
	for await (const e of s) events.push(e.type);
	return { events, message: await s.result() };
}

/** A sink that logs every chunk and the end, optionally slow. */
function sinkLog(log: string[] = []) {
	const chunks: unknown[] = [];
	const ends: { message: AssistantMessage; afterChunks: number }[] = [];
	const sink: ReadoutSink = {
		chunk: async (data) => {
			log.push("sink");
			chunks.push(data);
		},
		end: (message) => {
			ends.push({ message, afterChunks: chunks.length });
		},
	};
	return { chunks, ends, sink };
}

/** The `nomos` objects among the chunks, in order. */
function nomosIn(chunks: unknown[]): any[] {
	return chunks.flatMap((c) => (typeof c === "object" && c !== null && "nomos" in c ? [(c as { nomos: unknown }).nomos] : []));
}

test("provider: its events equal the stock provider's for the same stream, and no readout data reaches the message", async () => {
	const serve = await fakeServe(readoutHandler());
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		models.setProvider(
			createProvider({
				id: "plain",
				baseUrl: serve.url,
				auth: { apiKey: { name: "plain", resolve: async () => ({ auth: { apiKey: "x" } }) } },
				models: [{ ...nomosModel({ baseUrl: serve.url }), provider: "plain" }],
				api: { stream: stockStream as any, streamSimple: stockSimple as any },
			}),
		);
		const c = context();
		requests.register("s1", { block: BLOCK, sink: sinkLog().sink, last: c.messages.at(-1) });
		const ours = await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { context: c, sessionId: "s1" });
		const stock = await run(models, models.getModel("plain", NOMOS_MODEL)!, { sessionId: "s2" });
		assert.equal(serve.bodies[0].nomos !== undefined && serve.bodies[1].nomos === undefined, true, "only our request carried the block");
		assert.deepEqual(ours.events, stock.events);
		assert.deepEqual(ours.message.content, stock.message.content);
		assert.equal(ours.message.responseId, "chatcmpl-1");
		assert.equal(ours.message.stopReason, "stop");
		assert.ok(ours.events.includes("thinking_delta") && ours.events.includes("text_delta"));
		// Provider and model metadata may say "nomos"; readout data and readout keys may not (ruling R10, #1.3).
		const text = JSON.stringify(ours.message);
		for (const mark of [String(SENTINEL), '"proj"', '"readout"', '"summary"', "channel_prompt_v1"]) assert.ok(!text.includes(mark), `${mark} reached the message`);
	} finally {
		await serve.close();
	}
});

test("provider: the request carries the block, every chunk reaches the sink in order, and the sink sees the end last", async () => {
	const serve = await fakeServe(readoutHandler(6, 4));
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		const log = sinkLog();
		const c = context();
		requests.register("s1", { block: BLOCK, sink: log.sink, last: c.messages.at(-1) });
		await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { context: c, sessionId: "s1" });
		await waitFor(() => log.ends.length === 1);
		assert.deepEqual(serve.bodies[0].nomos, BLOCK);
		assert.equal(typeof serve.bodies[0].max_tokens, "number", "the serve reads max_tokens");
		assert.equal(log.chunks.length, 9, "the role, reasoning, six content and finish chunks");
		const nomos = nomosIn(log.chunks);
		assert.deepEqual(nomos.flatMap((n) => (n.readout ?? []).map((r: { pos: number }) => r.pos)), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
		assert.equal(nomos.filter((n) => n.header !== undefined).length, 1);
		assert.equal(nomos.filter((n) => n.summary !== undefined).length, 1);
		assert.deepEqual([log.ends[0].message.stopReason, log.ends[0].afterChunks], ["stop", 9]);
	} finally {
		await serve.close();
	}
});

test("provider: the stream waits for the sink: a chunk's text reaches Pi only after the sink took the chunk", async () => {
	const serve = await fakeServe(readoutHandler(6, 4));
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		let release: () => void = () => undefined;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		let holding: () => void = () => undefined;
		const held = new Promise<void>((resolve) => {
			holding = resolve;
		});
		const sink: ReadoutSink = {
			chunk: async (data) => {
				if (JSON.stringify(data).includes('"w2 "')) {
					holding();
					await gate;
				}
			},
			end: () => undefined,
		};
		const c = context();
		requests.register("s1", { block: BLOCK, sink, last: c.messages.at(-1) });
		const texts: string[] = [];
		const s = models.streamSimple(models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, c, { sessionId: "s1" });
		const reading = (async () => {
			for await (const e of s) if (e.type === "text_delta") texts.push(e.delta);
		})();
		await held;
		await new Promise((r) => setTimeout(r, 50));
		assert.ok(!texts.join("").includes("w2"), `w2 reached Pi before the sink took its chunk: ${texts.join("")}`);
		release();
		await reading;
		assert.ok(texts.join("").includes("w5"));
	} finally {
		await serve.close();
	}
});

test("provider: an observer the caller set still sees every chunk, before the sink", async () => {
	const serve = await fakeServe(readoutHandler(6, 4));
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		const order: string[] = [];
		const log = sinkLog(order);
		const c = context();
		requests.register("s1", { block: BLOCK, sink: log.sink, last: c.messages.at(-1) });
		await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, {
			context: c,
			sessionId: "s1",
			observer: async () => {
				order.push("observer");
			},
		});
		assert.deepEqual(order, Array.from({ length: 9 }, () => ["observer", "sink"]).flat());
	} finally {
		await serve.close();
	}
});

test("provider: a request nobody registered, or a call that does not send the registered last message, is the stock request", async () => {
	const serve = await fakeServe(readoutHandler());
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { sessionId: "s9" });
		const log = sinkLog();
		requests.register("s1", { block: BLOCK, sink: log.sink, last: { role: "user", content: "another call's message", timestamp: 1 } });
		await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { sessionId: "s1" });
		assert.deepEqual(serve.bodies.map((b) => b.nomos), [undefined, undefined]);
		assert.deepEqual([log.chunks.length, log.ends.length], [0, 0]);
		assert.equal(requests.withdraw("s1", log.sink), true, "the registration still waits for its own call");
	} finally {
		await serve.close();
	}
});

test("provider: a 400 for a bad nomos block is an error event, and the sink sees that end", async () => {
	const serve = await fakeServe(() => ({ status: 400, body: { detail: "nomos: steer.strength must be a finite number in [-2.0, 2.0] (ruling P2-R7)" } }));
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		const log = sinkLog();
		const c = context();
		requests.register("s1", { block: BLOCK, sink: log.sink, last: c.messages.at(-1) });
		const { events, message } = await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { context: c, sessionId: "s1" });
		assert.equal(events.at(-1), "error");
		assert.equal(message.stopReason, "error");
		assert.match(message.errorMessage ?? "", /400|nomos/);
		await waitFor(() => log.ends.length === 1);
		assert.equal(log.ends[0].message.stopReason, "error");
	} finally {
		await serve.close();
	}
});

test("provider: the fake serve refuses a non-string conversation as Plan 2's serve does (ruling R1)", async () => {
	const serve = await fakeServe(readoutHandler());
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		const c = context();
		requests.register("s1", { block: { ...BLOCK, conversation: 7 as unknown as string }, sink: sinkLog().sink, last: c.messages.at(-1) });
		const { message } = await run(models, models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, { context: c, sessionId: "s1" });
		assert.equal(message.stopReason, "error");
		assert.deepEqual(serve.refused, ["nomos: conversation must be a string of at most 256 characters"]);
	} finally {
		await serve.close();
	}
});

test("fake: Plan 2's off-mode summary (P2-R13) round-trips through the fake and TurnCheck, and its 400 matches Plan 2 byte for byte", async () => {
	const sha = "d".repeat(64);
	const block: NomosBlock = { steer: { layer: 24, directions: sha, direction: "calm", strength: 0.7 }, conversation: "17", turn: 4 };
	const expected = JSON.parse(`{"header": {"v": "nomos.readout/1", "conversation": "17", "turn": 4, "response_id": "chatcmpl-1", "settings": null,
		"steer": {"layer": 24, "directions": "${sha}", "direction": "calm", "strength": 0.7, "from_pos": 7}},
		"ranges": {"reused": [0, 0], "prompt": [0, 0], "generated": [0, 0]},
		"counts": {"records": 0, "emotion_valid": 0, "workspace_valid": 0},
		"missing": {"kv_reused": 0, "natural_stop": 1, "settle_trimmed": 0, "readout_off": 1}}`);
	const chunks = readoutHandler(6, 8)({ nomos: block }, 0).chunks ?? [];
	assert.deepEqual((chunks.at(-1) as { nomos?: unknown }).nomos, { summary: expected }, "the finish chunk carries P2-R13's summary");
	const check = new TurnCheck(block);
	const updates = chunks.flatMap((c) => check.take(c) ?? []);
	assert.deepEqual(updates, [{ header: null, records: [], summary: expected }]);
	const serve = await fakeServe(readoutHandler());
	try {
		const refused = await fetch(`${serve.url}/chat/completions`, { method: "POST", body: JSON.stringify({ messages: [], nomos: { conversation: 17 } }) });
		assert.equal(refused.status, 400);
		assert.equal(refused.headers.get("content-type"), "application/json");
		assert.equal(await refused.text(), '{"detail":"nomos: conversation must be a string of at most 256 characters"}');
	} finally {
		await serve.close();
	}
});

test("provider: aborting the request closes the serve connection, and the sink sees the abort", async () => {
	const many = Array.from({ length: 200 }, (_, i) => chunk("chatcmpl-1", { content: `w${i} ` }));
	const serve = await fakeServe(() => ({ chunks: [chunk("chatcmpl-1", { role: "assistant" }), ...many], delayMs: 20 }));
	try {
		const requests = new ReadoutRequests();
		const models = createModels();
		models.setProvider(nomosProvider({ baseUrl: serve.url, requests }));
		const log = sinkLog();
		const c = context();
		requests.register("s1", { block: BLOCK, sink: log.sink, last: c.messages.at(-1) });
		const ctl = new AbortController();
		const s = models.streamSimple(models.getModel(NOMOS_PROVIDER, NOMOS_MODEL)!, c, { sessionId: "s1", signal: ctl.signal });
		for await (const e of s) {
			if (e.type === "text_delta") ctl.abort();
		}
		await waitFor(() => serve.aborted() === 1, 3000);
		await waitFor(() => log.ends.length === 1);
		assert.equal(log.ends[0].message.stopReason, "aborted");
	} finally {
		await serve.close();
	}
});
