import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { openNodeSqliteStorage } from "../src/pi.ts";
import { AGENT, CONFIG, openAgent, STEER, turnsOf } from "./harness.ts";
import { ctx, fakeServe, readoutHandler } from "./support.ts";

test("SQLite: real close/reopen preserves records and settings without leaking projections to the next model request", async () => {
	const dir = await mkdtemp(path.join(os.tmpdir(), "glimmer-agent-sqlite-"));
	const serve = await fakeServe(readoutHandler());
	let active: Awaited<ReturnType<typeof openAgent>> | undefined;
	try {
		active = await openAgent(serve.url, await openNodeSqliteStorage(path.join(dir, "session.sqlite")));
		const root = await active.harness.root(ctx, AGENT);
		const id = root.id;
		await active.host.setReadout(root, CONFIG);
		await active.host.setSteer(root, { ...STEER, strength: -0.7 });
		assert.equal((await (await root.submit({ type: "input", content: "First" }, ctx)).wait(ctx)).status, "done");
		const before = await turnsOf(root);
		const settings = await active.host.settings(id);
		assert.equal(before.length, 1);
		assert.equal(before[0].status, "complete");
		await active.host.close();
		await active.harness.close(ctx);
		assert.deepEqual(active.host.errors, []);
		active = undefined;
		active = await openAgent(serve.url, await openNodeSqliteStorage(path.join(dir, "session.sqlite")));
		const resumed = await active.harness.conversation(id, ctx);
		assert.ok(resumed);
		assert.deepEqual(await turnsOf(resumed), before);
		assert.deepEqual(await active.host.settings(id), settings);
		assert.equal((await (await resumed.submit({ type: "input", content: "Second" }, ctx)).wait(ctx)).status, "done");
		const after = await turnsOf(resumed);
		assert.deepEqual(after.map((t) => t.status), ["complete", "complete"]);
		assert.deepEqual(after.map((t) => t.turn), [0, 1]);
		assert.equal(serve.bodies[1].nomos.steer.strength, -0.7);
		assert.ok(!JSON.stringify(serve.bodies[1].messages).includes('"proj"'));
		assert.deepEqual(active.host.errors, []);
	} finally {
		await active?.host.close();
		await active?.harness.close(ctx);
		await serve.close();
		await rm(dir, { recursive: true }); // only this test's exclusively created directory
	}
});
