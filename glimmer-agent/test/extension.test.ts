import assert from "node:assert/strict";
import { test } from "node:test";
import { BatchEntry, type ReadoutHost, ReadoutStateDoc, TurnEntry } from "../src/extension.ts";
import { type Conversation, defineExtension, GenerationTask, hook, MemoryStorage } from "../src/pi.ts";
import { AGENT, CONFIG, entries, openAgent, setup, STEER, testStorage, turnsOf, WORKSPACE } from "./harness.ts";
import { ctx, fakeServe, readoutHandler, SENTINEL, waitFor, within } from "./support.ts";

/** The record counts of the conversation's batches, in order. */
async function batchSizes(conversation: Parameters<typeof entries>[0]): Promise<number[]> {
	return (await entries(conversation)).flatMap((e) => (BatchEntry.is(e) ? [e.data.records.length] : []));
}

test("extension: with readouts and steering off the request has no nomos block and nothing is recorded", async () => {
	const t = await setup();
	try {
		await t.ask("hello");
		assert.equal(t.serve.bodies[0].nomos, undefined);
		assert.ok(!(await entries(t.root)).some((e) => e.kind.startsWith("glimmer.")));
	} finally {
		await t.close();
	}
});

test("extension: the first recorded turn asks for every prompt position, the next for new ones, a settings change for all again", async () => {
	const t = await setup();
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		await t.ask("two");
		await t.host.setReadout(t.root, { ...CONFIG, workspace: WORKSPACE });
		await t.ask("three");
		const blocks = t.serve.bodies.map((b) => b.nomos);
		assert.deepEqual(blocks.map((b) => b.readout.prompt), ["all", "new", "all"]);
		assert.deepEqual(blocks.map((b) => b.turn), [0, 1, 2]);
		assert.ok(blocks.every((b) => b.conversation === String(t.root.id)), "the wire carries String(conversationId) (ruling R1)");
		assert.deepEqual(blocks[2].readout.workspace, WORKSPACE);
		assert.equal(blocks[0].readout.workspace, undefined);
		assert.deepEqual((await turnsOf(t.root)).map((x) => [x.turn, x.status]), [[0, "complete"], [1, "complete"], [2, "complete"]]);
	} finally {
		await t.close();
	}
});

test("extension: a request queued during a reply sees that reply's turn record, under slow storage, with no wait between them", async () => {
	const t = await setup(readoutHandler(40, 4), testStorage({ delayMs: 5 }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		const one = await t.root.submit({ type: "input", content: "one" }, ctx);
		const two = await t.root.submit({ type: "input", content: "two" }, ctx); // queued behind "one", no wait between
		assert.equal((await one.wait(ctx)).status, "done");
		assert.equal((await two.wait(ctx)).status, "done");
		assert.deepEqual(t.serve.bodies.map((b) => [b.nomos.turn, b.nomos.readout.prompt]), [[0, "all"], [1, "new"]]);
		assert.deepEqual((await turnsOf(t.root)).map((x) => [x.turn, x.status, x.records.length]), [[0, "complete", 44], [1, "complete", 41]]);
	} finally {
		await t.close();
	}
});

test("extension: records land in batches of 16 and one complete turn record, and never reach the model", async () => {
	const t = await setup(readoutHandler(40, 4));
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		assert.deepEqual(await batchSizes(t.root), [16, 16, 12]);
		const [turn] = await turnsOf(t.root);
		assert.deepEqual([turn.status, turn.records.length, turn.summary?.header.response_id], ["complete", 44, "chatcmpl-1"]);
		assert.ok((await entries(t.root)).filter((e) => e.kind.startsWith("glimmer.")).every((e) => e.model === undefined));
		await t.ask("two");
		const sent = JSON.stringify(t.serve.bodies[1].messages);
		for (const mark of [String(SENTINEL), "glimmer.readout", '"pos"', '"proj"']) assert.ok(!sent.includes(mark), `${mark} reached the model`);
	} finally {
		await t.close();
	}
});

test("extension: a steering-only request is an unrecorded turn with its steering header, and it breaks recording continuity", async () => {
	const t = await setup();
	try {
		const steer = { ...STEER, strength: -0.7 };
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		await t.host.setReadout(t.root, { ...CONFIG, enabled: false });
		await t.host.setSteer(t.root, steer);
		await t.ask("two");
		assert.deepEqual(t.serve.bodies[1].nomos, { conversation: String(t.root.id), turn: 1, steer });
		const off = (await turnsOf(t.root))[1];
		assert.deepEqual(
			[off.status, off.recorded, off.records.length, off.header?.settings, off.header?.steer?.strength],
			["complete", false, 0, null, -0.7],
		);
		assert.deepEqual([off.summary?.ranges.generated, off.summary?.counts.records, off.summary?.missing.readout_off], [[0, 0], 0, 1], "P2-R13's summary");
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("three");
		assert.equal(t.serve.bodies[2].nomos.readout.prompt, "all", "the unrecorded turn broke continuity (ruling R3)");
		assert.deepEqual(t.serve.bodies[2].nomos.steer, steer);
		await t.host.setSteer(t.root, null);
		await t.host.setReadout(t.root, { ...CONFIG, enabled: false });
		await t.ask("four");
		assert.equal(t.serve.bodies[3].nomos, undefined);
	} finally {
		await t.close();
	}
});

test("extension: readouts off for one turn break continuity, so the same settings again ask for every prompt position", async () => {
	const t = await setup();
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		await t.host.setReadout(t.root, { ...CONFIG, enabled: false });
		await t.ask("two");
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("three");
		assert.equal(t.serve.bodies[1].nomos, undefined);
		assert.deepEqual([0, 2].map((i) => [t.serve.bodies[i].nomos.turn, t.serve.bodies[i].nomos.readout.prompt]), [[0, "all"], [1, "all"]]);
	} finally {
		await t.close();
	}
});

test("extension: a fork at a turn's record restores that turn's settings and counters, carries only its records, and starts its own lineage", async () => {
	const t = await setup();
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		const first = (await entries(t.root)).find((e) => TurnEntry.is(e) && e.data.turn === 0);
		assert.ok(first !== undefined, "turn 0 has a record");
		await t.host.setReadout(t.root, { ...CONFIG, workspace: WORKSPACE });
		await t.ask("two");
		const fork = await t.root.fork(first.id, { ownership: { kind: "ownerless" } }, ctx);
		assert.deepEqual((await t.host.settings(fork.id)).readout, CONFIG);
		const state = await t.harness.snapshot(ReadoutStateDoc, fork.id, ctx);
		assert.deepEqual([state?.turns, state?.continuity?.conversation, state?.open], [1, String(t.root.id), null], "the fork sees its turn's counters");
		assert.equal((await turnsOf(fork)).length, 1);
		const asked = await fork.submit({ type: "input", content: "three" }, ctx);
		assert.equal((await asked.wait(ctx)).status, "done");
		const block = t.serve.bodies[2].nomos;
		assert.deepEqual([block.conversation, block.turn, block.readout.prompt, block.readout.workspace], [String(fork.id), 1, "all", undefined]);
		assert.equal((await turnsOf(t.root)).length, 2, "the fork's turn is not the root's");
	} finally {
		await t.close();
	}
});

test("extension: an aborted reply keeps its header and every received record, as an aborted turn, and the next asks for every prompt position", async () => {
	const t = await setup(readoutHandler(18, 4, { 0: "hold" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		const input = await t.root.submit({ type: "input", content: "one" }, ctx);
		await waitFor(async () => (await batchSizes(t.root)).length > 0);
		await t.root.abort(ctx);
		await t.host.settled(t.root.id);
		assert.equal((await input.wait(ctx)).status, "unanswered");
		await waitFor(() => t.serve.aborted() === 1);
		const [turn] = await turnsOf(t.root);
		assert.deepEqual([turn.status, turn.summary, turn.header?.response_id], ["aborted", null, "chatcmpl-1"]);
		const sizes = await batchSizes(t.root);
		assert.equal(sizes[0], 16);
		assert.equal(sizes.reduce((a, b) => a + b, 0), turn.records.length, "every received record is flushed");
		const state = await t.harness.snapshot(ReadoutStateDoc, t.root.id, ctx);
		assert.deepEqual([state?.turns, state?.continuity, state?.open], [1, null, null]);
		await t.ask("two");
		assert.deepEqual([t.serve.bodies[1].nomos.turn, t.serve.bodies[1].nomos.readout.prompt], [1, "all"]);
	} finally {
		await t.close();
	}
});

test("extension: a reply that ends without a summary is a contract_error turn with its records, and the reply itself completes", async () => {
	const t = await setup(readoutHandler(6, 4, { 0: "no_summary" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		const [turn] = await turnsOf(t.root);
		assert.deepEqual([turn.status, turn.records.length, turn.summary, turn.header?.response_id], ["contract_error", 10, null, "chatcmpl-1"]);
		assert.match(turn.error ?? "", /without a readout summary/);
		await t.ask("two");
		assert.equal(t.serve.bodies[1].nomos.readout.prompt, "all");
	} finally {
		await t.close();
	}
});

test("extension: a malformed readout ends its turn as contract_error, durably, and the reply goes on", async () => {
	const t = await setup(readoutHandler(6, 4, { 0: "v2", 1: "bad_record", 2: "bad_counts", 3: "role_id", 4: "kv_reused" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		for (const q of ["one", "two", "three", "four", "five"]) await t.ask(q);
		const turns = await turnsOf(t.root);
		assert.deepEqual(turns.map((x) => [x.status, x.records.length]), [
			["contract_error", 0],
			["contract_error", 5],
			["contract_error", 9],
			["contract_error", 0],
			["contract_error", 9],
		]);
		assert.match(turns[0].error ?? "", /nomos\.readout\/2/);
		assert.match(turns[1].error ?? "", /emotion has 1 rows/);
		assert.match(turns[2].error ?? "", /summary\.counts/);
		assert.match(turns[3].error ?? "", /a chunk of completion chatcmpl-4 in completion chatcmpl-4-role/, "an ordinary chunk's ID counts (review F5)");
		assert.match(turns[4].error ?? "", /kv_reused is 1, the reused range holds 0/, "kv_reused matches the reused range (review F5)");
		assert.deepEqual(t.serve.bodies.map((b) => b.nomos.readout.prompt), ["all", "all", "all", "all", "all"]);
		const answers = (await entries(t.root)).filter((e) => e.kind === "pi.assistant");
		assert.ok(answers.length === 5 && answers.every((e) => JSON.stringify(e.model).includes("w5")), "every reply is whole");
	} finally {
		await t.close();
	}
});

test("extension: a retried attempt is a new request: the cut attempt is a transport_error turn with its records, the retry asks for every prompt position", async () => {
	const t = await setup(readoutHandler(6, 4, { 0: "cut" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		const turns = await turnsOf(t.root);
		assert.deepEqual(turns.map((x) => [x.turn, x.status, x.records.length]), [[0, "transport_error", 9], [1, "complete", 10]]);
		assert.equal(turns[0].header?.response_id, "chatcmpl-1");
		assert.notEqual(turns[0].request, turns[1].request);
		const batches = (await entries(t.root)).flatMap((e) => (BatchEntry.is(e) ? [[e.data.request, e.data.records.length]] : []));
		assert.deepEqual(batches, [[turns[0].request, 9], [turns[1].request, 10]]);
		assert.deepEqual(t.serve.bodies.map((b) => [b.nomos.turn, b.nomos.readout.prompt]), [[0, "all"], [1, "all"]]);
	} finally {
		await t.close();
	}
});

test("extension: a restart settles the request it cut from that request's batches, before the recovered attempt records its own", async () => {
	const serve = await fakeServe(readoutHandler(18, 4, { 0: "hold" }));
	const storage = testStorage();
	const a = await openAgent(serve.url, storage);
	const root = await a.harness.root(ctx, AGENT);
	await a.host.setReadout(root, CONFIG);
	const input = await root.submit({ type: "input", content: "one" }, ctx);
	await waitFor(async () => (await batchSizes(root)).length > 0);
	await a.host.close(); // the host records nothing more: the running request is left to the next start
	await a.harness.close(ctx);
	await a.host.settled(root.id);
	assert.equal(a.host.errors.length, 1);
	assert.match(a.host.errors[0].error, /after the host closed/);
	const b = await openAgent(serve.url, storage);
	try {
		await b.harness.waitForIdle(ctx); // scheduling resumes and recovery sends the request again
		const settled = await (await b.harness.submission(input.id, ctx))?.wait(ctx);
		assert.equal(settled?.status, "done");
		const reopened = await b.harness.conversation(root.id, ctx);
		assert.ok(reopened !== undefined);
		const turns = await turnsOf(reopened);
		assert.deepEqual(turns.map((x) => [x.turn, x.status, x.records.length]), [[0, "aborted", 16], [1, "complete", 22]]);
		assert.notEqual(turns[0].request, turns[1].request);
		assert.equal(turns[0].header?.response_id, "chatcmpl-1");
		assert.deepEqual([serve.bodies[1].nomos.turn, serve.bodies[1].nomos.readout.prompt], [1, "all"]);
		assert.deepEqual(b.host.errors, []);
	} finally {
		await b.host.close();
		await b.harness.close(ctx);
		await serve.close();
	}
});

test("extension: a summary is only a candidate: a readout object after it, or a stream cut after it, fails the turn (review F1)", async () => {
	const t = await setup(readoutHandler(6, 4, { 0: "late_nomos", 1: "summary_cut" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		await t.ask("two"); // its first attempt is cut after the summary; the retry completes
		const turns = await turnsOf(t.root);
		assert.deepEqual(turns.map((x) => [x.turn, x.status, x.records.length, x.summary === null]), [
			[0, "contract_error", 10, true],
			[1, "transport_error", 10, true],
			[2, "complete", 10, false],
		]);
		assert.match(turns[0].error ?? "", /a nomos object after the summary/);
		assert.match(turns[1].error ?? "", /ended without finish_reason/);
		assert.deepEqual(t.serve.bodies.map((b) => b.nomos.readout.prompt), ["all", "all", "all"]);
	} finally {
		await t.close();
	}
});

test("extension: an abort after the summary wins: the turn is aborted, not complete (review F1)", async () => {
	const t = await setup(readoutHandler(12, 4, { 0: "summary_hold" }));
	try {
		await t.host.setReadout(t.root, CONFIG);
		const input = await t.root.submit({ type: "input", content: "one" }, ctx);
		// The summary's chunk carries the 16th record, so the first batch is durable only once the summary was taken.
		await waitFor(async () => (await batchSizes(t.root)).length > 0);
		await t.root.abort(ctx);
		await t.host.settled(t.root.id);
		assert.equal((await input.wait(ctx)).status, "unanswered");
		const [turn] = await turnsOf(t.root);
		assert.deepEqual([turn.status, turn.summary, turn.records.length], ["aborted", null, 16]);
		const state = await t.harness.snapshot(ReadoutStateDoc, t.root.id, ctx);
		assert.equal(state?.continuity, null, "an aborted turn restores no continuity");
	} finally {
		await t.close();
	}
});

test("extension: an abort before the provider takes the request ends it as aborted, with no later request (review F2)", async () => {
	const serve = await fakeServe(readoutHandler());
	let root: Conversation | undefined;
	let aborting: Promise<void> | undefined;
	// Runs after the readout hook registered the request: abort the run, and go on once its signal fired. Pi then reads
	// the session ID from its cache and its model call's auth refuses the aborted signal, so the provider never takes
	// the request.
	const abortAfterRegistration = defineExtension({
		name: "abort-after-registration",
		hooks: [
			hook(GenerationTask, {
				beforeRequest: async (_request, _api, context) => {
					const signal = context.abortSignal;
					assert.ok(signal !== undefined, "the hook's context carries the generation's signal");
					aborting = root?.abort(ctx);
					if (!signal.aborted) await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
					return undefined;
				},
			}),
		],
	});
	const { host, harness } = await openAgent(serve.url, new MemoryStorage(), [abortAfterRegistration]);
	try {
		root = await harness.root(ctx, AGENT);
		await host.setReadout(root, CONFIG);
		const input = await root.submit({ type: "input", content: "one" }, ctx);
		assert.equal((await input.wait(ctx)).status, "unanswered");
		await aborting;
		await within(host.settled(root.id), 2000, "settled(): nothing ended the request the provider never took");
		const [turn] = await turnsOf(root);
		assert.deepEqual([turn?.status, turn?.records.length, turn?.header], ["aborted", 0, null]);
		assert.match(turn?.error ?? "", /before its readout request was sent/);
		assert.equal(serve.bodies.length, 0, "the provider never sent the request");
		const state = await harness.snapshot(ReadoutStateDoc, root.id, ctx);
		assert.deepEqual([state?.turns, state?.open, state?.continuity], [1, null, null]);
		assert.deepEqual(host.errors, []);
	} finally {
		await host.close();
		await harness.close(ctx);
		await serve.close();
	}
});

test("extension: closing the host records a request no provider call has taken yet, and joins it (review F2)", async () => {
	const serve = await fakeServe(readoutHandler());
	let host: ReadoutHost | undefined;
	const closeAfterRegistration = defineExtension({
		name: "close-after-registration",
		hooks: [
			hook(GenerationTask, {
				beforeRequest: async () => {
					await host?.close(); // the readout hook has registered the request; no provider call has taken it
					return undefined;
				},
			}),
		],
	});
	const agent = await openAgent(serve.url, new MemoryStorage(), [closeAfterRegistration]);
	host = agent.host;
	try {
		const root = await agent.harness.root(ctx, AGENT);
		await agent.host.setReadout(root, CONFIG);
		assert.equal((await (await root.submit({ type: "input", content: "one" }, ctx)).wait(ctx)).status, "done");
		const [turn] = await turnsOf(root);
		assert.deepEqual([turn?.status, turn?.records.length], ["aborted", 0]);
		assert.match(turn?.error ?? "", /the host closed before its readout request was sent/);
		assert.equal(serve.bodies[0].nomos, undefined, "the provider sent the request without readouts");
	} finally {
		await agent.harness.close(ctx);
		await serve.close();
	}
});

test("extension: a close that lands while a preparation awaits its write-ahead commit joins it and records its request (ruling A10)", async () => {
	const serve = await fakeServe(readoutHandler());
	let reached: () => void = () => undefined;
	const holding = new Promise<void>((resolve) => {
		reached = resolve;
	});
	let release: () => void = () => undefined;
	const released = new Promise<void>((resolve) => {
		release = resolve;
	});
	let held = false;
	// Hold the commit that creates glimmer.readout.state: on a new conversation with readouts on, the write-ahead.
	const storage = testStorage({
		hold: (writes) => {
			if (held || !writes.some((w) => w.type === "document.create" && w.record.kind === "glimmer.readout.state")) return undefined;
			held = true;
			reached();
			return released;
		},
	});
	const { host, harness } = await openAgent(serve.url, storage);
	try {
		const root = await harness.root(ctx, AGENT);
		await host.setReadout(root, CONFIG);
		const input = await root.submit({ type: "input", content: "one" }, ctx);
		await within(holding, 2000, "the write-ahead commit never started");
		const closing = host.close();
		const early = await Promise.race([closing.then(() => "closed"), new Promise<string>((resolve) => setTimeout(() => resolve("pending"), 50))]);
		assert.equal(early, "pending", "close() returned while a preparation it admitted awaited its write-ahead");
		release();
		await within(closing, 2000, "close() never finished");
		assert.equal(host.closed, true);
		const [turn, ...more] = await turnsOf(root); // close() joined the request's terminal commit
		assert.deepEqual([turn?.status, turn?.records.length, more.length], ["aborted", 0, 0]);
		assert.match(turn?.error ?? "", /the host closed before its readout request was sent/);
		assert.equal((await input.wait(ctx)).status, "done");
		assert.equal(serve.bodies[0].nomos, undefined, "the provider sent the request without readouts");
		const state = await harness.snapshot(ReadoutStateDoc, root.id, ctx);
		assert.deepEqual([state?.turns, state?.open, state?.continuity], [1, null, null]);
		assert.deepEqual(host.errors, []);
	} finally {
		release();
		await host.close();
		await harness.close(ctx);
		await serve.close();
	}
});

test("extension: once closing began, a request is not admitted: it goes without readouts, is reported, and breaks continuity (ruling A10)", async () => {
	const serve = await fakeServe(readoutHandler());
	const { host, harness } = await openAgent(serve.url);
	try {
		const root = await harness.root(ctx, AGENT);
		await host.setReadout(root, CONFIG);
		assert.equal((await (await root.submit({ type: "input", content: "one" }, ctx)).wait(ctx)).status, "done");
		assert.notEqual((await harness.snapshot(ReadoutStateDoc, root.id, ctx))?.continuity ?? null, null, "a complete turn keeps continuity");
		await host.close();
		assert.equal((await (await root.submit({ type: "input", content: "two" }, ctx)).wait(ctx)).status, "done");
		assert.equal(serve.bodies[1].nomos, undefined, "the request went without readouts");
		assert.deepEqual((await turnsOf(root)).map((x) => [x.turn, x.status]), [[0, "complete"]], "nothing is recorded for it");
		const state = await harness.snapshot(ReadoutStateDoc, root.id, ctx);
		assert.deepEqual([state?.turns, state?.open, state?.continuity], [1, null, null], "an unrecorded turn breaks continuity (ruling R3)");
		assert.deepEqual(host.errors.map((e) => e.error), ["the readout host is closing: this request goes without readouts or steering"]);
	} finally {
		await harness.close(ctx);
		await serve.close();
	}
});

test("extension: a fork at a batch of a turn's final transaction settles that inherited request from its batches (ruling A8)", async () => {
	const t = await setup(); // 10 records: the turn's only batch commits in its final transaction, beside its turn record
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		const [parent] = await turnsOf(t.root);
		const finalBatch = (await entries(t.root)).find((e) => BatchEntry.is(e) && e.data.request === parent.request);
		assert.ok(finalBatch !== undefined);
		const fork = await t.root.fork(finalBatch.id, { ownership: { kind: "ownerless" } }, ctx);
		const inherited = await t.harness.snapshot(ReadoutStateDoc, fork.id, ctx);
		assert.deepEqual([inherited?.turns, inherited?.open, (await turnsOf(fork)).length], [1, null, 0], "closed state, no turn record");
		const asked = await fork.submit({ type: "input", content: "two" }, ctx);
		assert.equal((await asked.wait(ctx)).status, "done");
		const turns = await turnsOf(fork);
		assert.deepEqual(turns.map((x) => [x.request === parent.request, x.turn, x.status, x.records.length]), [
			[true, 0, "aborted", 10],
			[false, 1, "complete", 10],
		]);
		assert.deepEqual([t.serve.bodies[1].nomos.turn, t.serve.bodies[1].nomos.readout.prompt], [1, "all"]);
		assert.deepEqual((await turnsOf(t.root)).map((x) => x.status), ["complete"], "the parent's record is untouched");
	} finally {
		await t.close();
	}
});
