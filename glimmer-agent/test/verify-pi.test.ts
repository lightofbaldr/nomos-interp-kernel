/**
 * The spec's first plan task (section 4): verify pi-durable 1.3.0's behaviors on a fake model before building on them.
 * 1. May the host commit app-defined records while a reply is still running, and do they stay out of model context?
 * 2. Is one transaction's display entry plus rewindable document change a fork boundary: does an as-of read or a fork at
 *    that entry see the change (the Pi persistence lifecycle, 2026-10-24)?
 * 3. Is afterResponse awaited before the answer commits, so that a commit made inside it lands first (the completion
 *    barrier)?
 * 4. Ruling P3-R2: does a beforeRequest hook read the provider session ID that pi-ai hands the provider? Ruling P3-R9:
 *    is the hook's last message the very object the provider call receives?
 * 5. Review F2: does beforeRequest's context carry the generation's own signal, so that an abort after the hook fires it
 *    and no provider call follows?
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxText } from "@earendil-works/pi-ai";
import {
	BACKGROUND_CONTEXT as ctx,
	type Conversation,
	createRegistry,
	defineDoc,
	defineEntry,
	defineExtension,
	GenerationTask,
	Harness,
	type HarnessT,
	hook,
	MemoryStorage,
	ProviderDoc,
} from "../src/pi.ts";

const ProbeDoc = defineDoc<{ n: number }>({
	kind: "glimmer.probe.state",
	version: 1,
	scope: "conversation",
	history: "rewindable",
	fork: "asOf",
	initial: () => ({ n: 0 }),
});
const ProbeEntry = defineEntry<{ secret: string }>("glimmer.probe");
const AfterEntry = defineEntry<{ at: string }>("glimmer.probe.after");

test("verify: a mid-run transaction appends a display entry and changes a rewindable document together; the entry stays out of model context and is a fork boundary", async () => {
	const faux = fauxProvider({ tokensPerSecond: 40 });
	const models = createModels();
	models.setProvider(faux.provider);
	const seen: string[] = [];
	let inRequest: () => void = () => undefined;
	const requested = new Promise<void>((resolve) => {
		inRequest = resolve;
	});
	faux.setResponses([
		(context) => {
			seen.push(JSON.stringify(context.messages));
			inRequest();
			return fauxAssistantMessage(fauxText("a first answer that streams for a while. ".repeat(6)));
		},
		(context) => {
			seen.push(JSON.stringify(context.messages));
			return fauxAssistantMessage(fauxText("second"));
		},
	]);
	const harness = await Harness.open(new MemoryStorage(), { models, registry: createRegistry() }, ctx);
	try {
		const model = faux.getModel();
		const root = await harness.root(ctx, { agent: { model: { provider: model.provider, modelId: model.id } } });
		const input = await root.submit({ type: "input", content: "hello" }, ctx);
		await requested; // the generation has sent its request: the run is in progress
		const probe = await root.commit(async (tx) => {
			const entry = await tx.appendEntry(ProbeEntry, root.id, { data: { secret: "READOUT-SENTINEL" } });
			(await tx.doc(ProbeDoc, root.id)).n = 1;
			return entry.id;
		}, ctx);
		assert.notEqual((await input.status(ctx)).status, "done", "the run ended before the write: the test proves nothing");
		assert.equal((await input.wait(ctx)).status, "done");
		const watch = await root.watch(ctx);
		const kinds = watch.value.entries.map((e) => e.kind);
		await watch.stop();
		assert.ok(kinds.indexOf("glimmer.probe") < kinds.lastIndexOf("pi.assistant"), `the write was appended mid-run: ${kinds}`);
		assert.equal((await (await root.submit({ type: "input", content: "again" }, ctx)).wait(ctx)).status, "done");
		assert.equal(seen.length, 2);
		assert.ok(!seen[1].includes("READOUT-SENTINEL"), "a display-only entry reached the model");
		assert.equal((await harness.snapshotAsOf(ProbeDoc, root.id, probe, ctx))?.n, 1, "the entry's own commit is in its as-of state");
		const fork = await root.fork(probe, { ownership: { kind: "ownerless" } }, ctx);
		assert.equal((await harness.snapshot(ProbeDoc, fork.id, ctx))?.n, 1, "a fork at the entry sees the document change");
	} finally {
		await harness.close(ctx);
	}
});

test("verify: afterResponse is awaited before the answer commits; beforeRequest sees the provider's session ID and its last message object", async () => {
	const faux = fauxProvider();
	const models = createModels();
	models.setProvider(faux.provider);
	const provided: { sessionId: string | undefined; last: unknown }[] = [];
	faux.setResponses([
		(context, options) => {
			provided.push({ sessionId: options?.sessionId, last: context.messages.at(-1) });
			return fauxAssistantMessage(fauxText("answer"));
		},
	]);
	const hooked: { sessionId: string; last: unknown }[] = [];
	let release: () => void = () => undefined;
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	let arrived: () => void = () => undefined;
	const atHook = new Promise<void>((resolve) => {
		arrived = resolve;
	});
	let harness: HarnessT | undefined;
	const registry = createRegistry();
	registry.install(
		defineExtension({
			name: "probe",
			hooks: [
				hook(GenerationTask, {
					beforeRequest: async (request, api, context) => {
						const provider = await api.snapshot(ProviderDoc, api.conversationId, context);
						hooked.push({ sessionId: provider?.sessionId ?? "absent", last: request.messages.at(-1) });
						return undefined;
					},
					afterResponse: async (_message, api) => {
						arrived();
						await gate;
						const conversation = await harness?.conversation(api.conversationId, ctx);
						await conversation?.commit(async (tx) => {
							await tx.appendEntry(AfterEntry, api.conversationId, { data: { at: "afterResponse" } });
						}, ctx);
					},
				}),
			],
		}),
	);
	harness = await Harness.open(new MemoryStorage(), { models, registry }, ctx);
	try {
		const model = faux.getModel();
		const root = await harness.root(ctx, { agent: { model: { provider: model.provider, modelId: model.id } } });
		const input = await root.submit({ type: "input", content: "hello" }, ctx);
		await atHook;
		await new Promise((resolve) => setTimeout(resolve, 50));
		assert.notEqual((await input.status(ctx)).status, "done", "the answer committed while afterResponse still ran");
		release();
		assert.equal((await input.wait(ctx)).status, "done");
		const watch = await root.watch(ctx);
		const kinds = watch.value.entries.map((e) => e.kind);
		await watch.stop();
		const after = kinds.indexOf("glimmer.probe.after");
		assert.ok(after >= 0 && after < kinds.lastIndexOf("pi.assistant"), `afterResponse's entry precedes the answer: ${kinds}`);
		assert.equal(provided.length, 1);
		assert.ok(provided[0].sessionId, "pi-durable forwards a session id");
		assert.equal(hooked[0].sessionId, provided[0].sessionId);
		assert.equal(hooked[0].last, provided[0].last, "the provider call carries the hook's last message object");
	} finally {
		await harness.close(ctx);
	}
});

test("verify: beforeRequest's context carries the generation's signal: an abort after the hook fires it, and no provider call follows", async () => {
	const faux = fauxProvider();
	const models = createModels();
	models.setProvider(faux.provider);
	let calls = 0;
	faux.setResponses([
		() => {
			calls += 1;
			return fauxAssistantMessage(fauxText("answer"));
		},
	]);
	let root: Conversation | undefined;
	let aborting: Promise<void> | undefined;
	let fired = false;
	const registry = createRegistry();
	registry.install(
		defineExtension({
			name: "probe",
			hooks: [
				hook(GenerationTask, {
					beforeRequest: async (_request, _api, context) => {
						const signal = context.abortSignal;
						if (signal === undefined) return undefined; // the assertions below then fail
						aborting = root?.abort(ctx);
						if (!signal.aborted) await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
						fired = true;
						return undefined;
					},
				}),
			],
		}),
	);
	const harness = await Harness.open(new MemoryStorage(), { models, registry }, ctx);
	try {
		const model = faux.getModel();
		root = await harness.root(ctx, { agent: { model: { provider: model.provider, modelId: model.id } } });
		const input = await root.submit({ type: "input", content: "hello" }, ctx);
		assert.equal((await input.wait(ctx)).status, "unanswered");
		await aborting;
		assert.equal(fired, true, "the hook's context signal fired when the run was aborted");
		assert.equal(calls, 0, "no provider call followed the abort");
	} finally {
		await harness.close(ctx);
	}
});
