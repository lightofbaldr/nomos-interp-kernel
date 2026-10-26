/**
 * Readout spine through Pi on the GPU: a two-turn conversation against the kernel worktree's serve (scripts/e2e-gpu.sh
 * starts it), with records arriving through the bridge. Turn 1 records the whole prompt; turn 2 reuses the KV store.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createModels } from "@earendil-works/pi-ai";
import { type BridgeMessage, openBridge } from "../src/bridge.ts";
import { ReadoutHost, readoutExtension, type ReadoutTurnRecord } from "../src/extension.ts";
import { BACKGROUND_CONTEXT as ctx, createRegistry, Harness, openNodeSqliteStorage } from "../src/pi.ts";
import { NOMOS_MODEL, NOMOS_PROVIDER, nomosProvider } from "../src/provider.ts";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8191/v1";
const work = process.argv[3];
assert.ok(work && path.isAbsolute(work), "a fresh absolute work directory is required");
const assets = JSON.parse(fs.readFileSync(path.join(os.homedir(), "nomos_data/readout-assets/registry.json"), "utf8")).assets as { id: string; kind: string }[];
const directions = assets.find((a) => a.kind === "directions")!.id;
const lens = assets.find((a) => a.kind === "lens")!.id;

const host = new ReadoutHost(ctx);
const models = createModels();
models.setProvider(nomosProvider({ baseUrl, requests: host.requests, contextWindow: 32768, maxTokens: 512 }));
const registry = createRegistry();
registry.install(readoutExtension(host));
const harness = await Harness.open(await openNodeSqliteStorage(path.join(work, "session.sqlite")), { models, registry }, ctx);
host.attach(harness);
try {
	const root = await harness.root(ctx, { agent: { model: { provider: NOMOS_PROVIDER, modelId: NOMOS_MODEL } } });
	const got: BridgeMessage[] = [];
	const bridge = await openBridge(harness, host, root.id, ctx, (m) => {
		got.push(m);
	});
	await host.setReadout(root, { enabled: true, emotion: { layers: [20, 24, 29], directions }, workspace: { layers: [36, 40, 44, 48], lens, k: 8 } });
	const questions = ["In two sentences: what is the capital of Portugal, and what is it known for?", "And its population?"];
	for (const [i, q] of questions.entries()) {
		const settled = await (await root.submit({ type: "input", content: q }, ctx)).wait(ctx);
		assert.equal(settled.status, "done", `turn ${i + 1} was not answered`);
	}
	// Each turn record commits before its answer (afterResponse joins it). The bridge delivers it on its own schedule, and
	// stop() drops frames it has not delivered, so wait for both records first.
	const deadline = Date.now() + 30_000;
	while (got.filter((m) => m.type === "readout.turn").length < 2) {
		if (Date.now() > deadline) throw new Error("the bridge did not deliver both turn records");
		await new Promise((r) => setTimeout(r, 50));
	}
	await bridge.stop();
	const turns = got.flatMap((m) => (m.type === "readout.turn" ? [m.turn] : [])) as ReadoutTurnRecord[];
	assert.deepEqual(turns.map((t) => t.status), ["complete", "complete"], JSON.stringify(turns.map((t) => t.error)));
	const summaries = turns.map((t) => t.summary!);
	assert.equal(summaries[0].ranges.prompt[0], 0, "turn 1 records the whole prompt");
	assert.ok(summaries[1].ranges.prompt[0] > 0, "turn 2 reuses the KV store");
	for (const [i, t] of turns.entries()) {
		const gen = t.records.filter((r) => r.source === "generated");
		assert.ok(gen.length > 0 && gen.every((r) => r.emotion !== null && r.workspace !== null), "every generated record has both readouts");
		assert.equal(summaries[i].counts.records, t.records.length);
	}
	assert.deepEqual(host.errors, []);
	console.log(JSON.stringify(turns.map((t, i) => ({ ranges: summaries[i].ranges, missing: summaries[i].missing, records: t.records.length }))));
	fs.writeFileSync(path.join(work, "result.json"), JSON.stringify({ turns: turns.map((t) => ({ status: t.status, header: t.header, summary: t.summary, records: t.records.length })), bridgeEvents: got.length, errors: host.errors }, null, 2) + "\n", { flag: "wx" });
	console.log("PI E2E OK");
} finally {
	await host.close();
	await harness.close(ctx);
}
