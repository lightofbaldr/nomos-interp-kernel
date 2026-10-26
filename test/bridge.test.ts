import assert from "node:assert/strict";
import { test } from "node:test";
import { type BridgeMessage, openBridge } from "../src/bridge.ts";
import { type SettingsMessage, TurnEntry } from "../src/extension.ts";
import { AGENT, CONFIG, entries, openAgent, setup, STEER } from "./harness.ts";
import { ctx, fakeServe, readoutHandler, waitFor } from "./support.ts";

type Of<T extends BridgeMessage["type"]> = Extract<BridgeMessage, { type: T }>;

function settingsIn(got: BridgeMessage[]): SettingsMessage[] {
	return got.filter((m): m is SettingsMessage => m.type === "settings");
}

test("bridge: Pi's events, each readout entry in order, and settings changes arrive on one stream", async () => {
	const t = await setup(readoutHandler(40, 4));
	try {
		const got: BridgeMessage[] = [];
		const bridge = await openBridge(t.harness, t.host, t.root.id, ctx, (m) => {
			got.push(m);
		});
		assert.deepEqual(bridge.snapshot.settings, {
			type: "settings",
			conversationId: t.root.id,
			version: 0,
			readout: { enabled: false, emotion: null, workspace: null },
			steer: null,
		});
		await t.host.setReadout(t.root, CONFIG);
		await t.ask("one");
		await waitFor(() => got.some((m) => m.type === "readout.turn"));
		await bridge.stop();
		const types = got.map((m) => m.type);
		assert.equal(types[0], "settings");
		assert.ok(types.includes("pi"), "Pi's own events pass through");
		const batches = got.filter((m): m is Of<"readout.batch"> => m.type === "readout.batch");
		assert.deepEqual(batches.map((m) => m.batch.records.length), [16, 16, 12]);
		const positions = batches.flatMap((m) => m.batch.records.map((r) => r.pos));
		assert.deepEqual(positions, [...positions].sort((a, b) => a - b), "records arrive in position order");
		assert.ok(types.lastIndexOf("readout.batch") < types.indexOf("readout.turn"));
		const turn = got.find((m): m is Of<"readout.turn"> => m.type === "readout.turn");
		assert.deepEqual([turn?.turn.status, turn?.turn.records.length], ["complete", 44]);
	} finally {
		await t.close();
	}
});

test("bridge: a reconnect starts from the current settings, changes made while it was away included", async () => {
	const t = await setup();
	try {
		const first: BridgeMessage[] = [];
		const a = await openBridge(t.harness, t.host, t.root.id, ctx, (m) => {
			first.push(m);
		});
		await t.host.setSteer(t.root, STEER);
		await a.stop();
		await t.host.setReadout(t.root, CONFIG); // no bridge is attached
		const later: BridgeMessage[] = [];
		const b = await openBridge(t.harness, t.host, t.root.id, ctx, (m) => {
			later.push(m);
		});
		assert.deepEqual(b.snapshot.settings, { type: "settings", conversationId: t.root.id, version: 2, readout: CONFIG, steer: STEER });
		await t.host.setSteer(t.root, null);
		await b.stop();
		assert.deepEqual(settingsIn(first).map((m) => m.version), [1]);
		assert.deepEqual(settingsIn(later).map((m) => [m.version, m.steer]), [[3, null]]);
	} finally {
		await t.close();
	}
});

test("bridge: attaching to a fork starts from the fork's settings and records, and the parent's later changes stay out", async () => {
	const t = await setup();
	try {
		await t.host.setReadout(t.root, CONFIG);
		await t.host.setSteer(t.root, STEER);
		await t.ask("one");
		const at = (await entries(t.root)).find((e) => TurnEntry.is(e));
		assert.ok(at !== undefined);
		await t.host.setSteer(t.root, { ...STEER, strength: -1.5 });
		const fork = await t.root.fork(at.id, { ownership: { kind: "ownerless" } }, ctx);
		const got: BridgeMessage[] = [];
		const bridge = await openBridge(t.harness, t.host, fork.id, ctx, (m) => {
			got.push(m);
		});
		const s = bridge.snapshot.settings;
		assert.deepEqual([s.conversationId, s.version, s.readout, s.steer], [fork.id, 2, CONFIG, STEER]);
		assert.ok(bridge.snapshot.pi.entries.some((e) => TurnEntry.is(e)), "the fork carries the turn record it was taken at");
		await t.host.setSteer(t.root, null);
		await t.host.setSteer(fork, { ...STEER, strength: 0.3 });
		await bridge.stop();
		assert.deepEqual(settingsIn(got).map((m) => [m.conversationId, m.version, m.steer?.strength]), [[fork.id, 3, 0.3]]);
	} finally {
		await t.close();
	}
});

test("bridge: settings changes racing the attachment arrive once each, in version order, ending at the newest", async () => {
	const t = await setup();
	try {
		const got: BridgeMessage[] = [];
		const changing = Promise.all([0.1, 0.2, 0.3, 0.4, 0.5].map((strength) => t.host.setSteer(t.root, { ...STEER, strength })));
		const bridge = await openBridge(t.harness, t.host, t.root.id, ctx, (m) => {
			got.push(m);
		});
		await changing;
		await bridge.stop();
		const seen = [bridge.snapshot.settings, ...settingsIn(got)];
		const versions = seen.map((m) => m.version);
		assert.deepEqual(versions, [...new Set(versions)].sort((a, b) => a - b), `once each, in order: ${versions}`);
		const newest = await t.host.settings(t.root.id);
		assert.equal(newest.version, 5);
		assert.deepEqual([seen.at(-1)?.version, seen.at(-1)?.steer], [5, newest.steer]);
	} finally {
		await t.close();
	}
});

test("bridge: a failed turn arrives as its readout.turn, and a failure the host could not record as readout.error", async () => {
	const serve = await fakeServe(readoutHandler(18, 4, { 0: "v2", 1: "hold" }));
	const { host, harness } = await openAgent(serve.url);
	try {
		const root = await harness.root(ctx, AGENT);
		const got: BridgeMessage[] = [];
		const bridge = await openBridge(harness, host, root.id, ctx, (m) => {
			got.push(m);
		});
		await host.setReadout(root, CONFIG);
		assert.equal((await (await root.submit({ type: "input", content: "one" }, ctx)).wait(ctx)).status, "done");
		await waitFor(() => got.some((m) => m.type === "readout.turn"));
		assert.equal(got.find((m): m is Of<"readout.turn"> => m.type === "readout.turn")?.turn.status, "contract_error");
		await root.submit({ type: "input", content: "two" }, ctx);
		await waitFor(() => got.some((m) => m.type === "readout.batch"));
		await host.close();
		await harness.close(ctx); // the held request ends after the host closed: nothing can record it now
		await waitFor(() => got.some((m) => m.type === "readout.error"));
		assert.match(got.find((m): m is Of<"readout.error"> => m.type === "readout.error")?.error ?? "", /after the host closed/);
		await bridge.stop();
	} finally {
		await host.close();
		await harness.close(ctx);
		await serve.close();
	}
});

test("bridge: messages go out in order through one chain: a slow send holds the next, and stop() waits for both (ruling A9)", async () => {
	const t = await setup();
	try {
		let release: () => void = () => undefined;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		const delivered: number[] = [];
		const bridge = await openBridge(t.harness, t.host, t.root.id, ctx, async (m) => {
			if (m.type !== "settings") return;
			if (m.version === 1) await gate; // version 1's send is slow
			delivered.push(m.version);
		});
		await t.host.setSteer(t.root, STEER);
		await t.host.setSteer(t.root, { ...STEER, strength: 0.3 });
		let stopped = false;
		const stopping = bridge.stop().then(() => {
			stopped = true;
		});
		await new Promise((resolve) => setTimeout(resolve, 30));
		assert.deepEqual([delivered, stopped], [[], false], "version 2 waits behind version 1, and stop() waits for both");
		release();
		await stopping;
		assert.deepEqual(delivered, [1, 2]);
	} finally {
		await t.close();
	}
});

test("bridge: a rejected send ends delivery, and stop() rejects with it (ruling A9)", async () => {
	const t = await setup();
	try {
		const tried: number[] = [];
		const bridge = await openBridge(t.harness, t.host, t.root.id, ctx, async (m) => {
			if (m.type !== "settings") return;
			tried.push(m.version);
			throw new Error("the client went away");
		});
		await t.host.setSteer(t.root, STEER);
		await t.host.setSteer(t.root, null);
		await assert.rejects(bridge.stop(), /the client went away/);
		assert.deepEqual(tried, [1], "nothing is sent after the failed send");
	} finally {
		await t.close();
	}
});
