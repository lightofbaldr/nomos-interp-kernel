import assert from "node:assert/strict";
import { test } from "node:test";
import { ProfileHost, ProfileDoc, ProfileUsedEntry, profileExtension, profileDigest } from "../src/profile.ts";
import { emptyContent, parseSkill, validateContent, profileSize } from "../src/profile-model.ts";
import { AgentDoc, type EntryId, type Storage } from "../src/pi.ts";
import { openAgent, AGENT, entries, testStorage, CONFIG } from "./harness.ts";
import { ctx, fakeServe, readoutHandler, waitFor } from "./support.ts";

const skill = { markdown: "---\nname: count_files\ndescription: Count precisely\n---\nSKILL_SENTINEL", enabled: true };
async function setup(storage?: Storage, hold = false) {
	const serve = await fakeServe(readoutHandler(4, 4, hold ? { 0: "hold" } : {}));
	const profiles = new ProfileHost(ctx), agent = await openAgent(serve.url, storage, [profileExtension(profiles)]);
	profiles.attach(agent.harness);
	const chat = await agent.harness.root(ctx, { ...AGENT, agent: { ...AGENT.agent, tools: [], instructions: "LEGACY" } });
	return { ...agent, profiles, chat, serve, close: async () => { await agent.host.close(); await agent.harness.close(ctx); await serve.close(); } };
}
test("profile model: sizes, restricted frontmatter and disabled/duplicate skills", () => {
	assert.equal(parseSkill(skill.markdown).name, "count_files");
	assert.equal(parseSkill("---\nname: n\ndescription: >-\n  two\n  lines\n---\nbody").description, "two lines");
	for (const value of ["bad", skill.markdown.replace("name:", "hooks:"), skill.markdown.replace("Count precisely", "&alias unsafe")]) assert.throws(() => parseSkill(value));
	const p = { ...emptyContent("base"), skills: [skill] }; assert.equal(validateContent(p).skills.length, 1);
	assert.deepEqual(validateContent(new Proxy(p, {})), p);
	assert.equal(profileDigest(validateContent({ allowLarge: false, advisory: [], skills: [skill], instructions: "base" })), profileDigest(validateContent(p)));
	assert.throws(() => validateContent({ ...p, skills: [skill, skill] }), /Duplicate/);
	assert.throws(() => validateContent({ ...p, tools: ["host_exec"] }), /Unknown/);
	assert.throws(() => validateContent(emptyContent("x".repeat(6000))), /6,000/);
	assert.ok(profileSize(validateContent({ ...emptyContent("x".repeat(6000)), allowLarge: true })).approximateTokens > 1500);
	assert.throws(() => validateContent({ ...emptyContent("x".repeat(66000)), allowLarge: true }));
	assert.throws(() => validateContent(emptyContent("\ud800")));
});
test("profiles: preserve legacy, native atomic config, stale edit refusal, exact request/readout attribution and non-model metadata", async () => {
	const t = await setup(); try {
		const initial = await t.profiles.get(t.chat); assert.equal(initial.active?.content.instructions, "LEGACY");
		const content = { ...emptyContent("INSTRUCTIONS_SENTINEL"), skills: [skill] };
		const saved = await t.profiles.save(t.chat, initial.version, content);
		await assert.rejects(t.profiles.save(t.chat, initial.version, emptyContent("stale")), /Reload/);
		assert.equal((await t.harness.snapshot(AgentDoc, t.chat.id, ctx))?.instructions, content.instructions);
		await t.host.setReadout(t.chat, CONFIG);
		assert.equal((await (await t.profiles.submit(t.chat, "one")).wait(ctx)).status, "done");
		let all = await entries(t.chat), used = all.filter(ProfileUsedEntry.is); assert.equal(used.length, 1);
		assert.equal(used[0].data.profile.digest, profileDigest(content));
		assert.equal(used[0].data.profile.revision, saved.active?.revision);
		const turn = all.find(e => e.kind === "glimmer.readout.turn")!;
		assert.equal(used[0].data.request, (turn.data as { request: string }).request);
		const sent = JSON.stringify(t.serve.bodies[0]); assert.match(sent, /SKILL_SENTINEL/); assert.match(sent, /INSTRUCTIONS_SENTINEL/);
		assert.ok(sent.indexOf("SKILL_SENTINEL") < sent.indexOf("INSTRUCTIONS_SENTINEL"));
		assert.equal(t.serve.bodies[0].tools?.length ?? 0, 0);
		await (await t.profiles.submit(t.chat, "two")).wait(ctx);
		assert.doesNotMatch(JSON.stringify(t.serve.bodies[1].messages), /glimmer\.profile|text-only-1|"digest"/);
		all = await entries(t.chat); assert.ok(all.filter(e => e.kind.startsWith("glimmer.profile")).every(e => e.model === undefined));
		assert.equal(all.filter(ProfileUsedEntry.is).length, 2);
	} finally { await t.close(); }
});
test("profiles: held run stages edits, next input activates once; fork clears a pending draft and restore creates a new revision", async () => {
	const t = await setup(undefined, true); try {
		await t.host.setReadout(t.chat, CONFIG);
		const start = await t.profiles.get(t.chat), submission = await t.profiles.submit(t.chat, "hold");
		await waitFor(() => t.serve.bodies.length === 1);
		const pending = await t.profiles.save(t.chat, start.version, emptyContent("NEXT"));
		assert.equal(pending.active?.content.instructions, "LEGACY"); assert.equal(pending.pending?.content.instructions, "NEXT");
		assert.equal((await t.harness.snapshot(AgentDoc, t.chat.id, ctx))?.instructions, "LEGACY");
		await assert.rejects(t.profiles.submit(t.chat, "forbidden"), /run/);
		const atPending = (await entries(t.chat)).at(-1)!.id;
		const fork = await t.profiles.fork(t.chat, atPending);
		const fs = await t.profiles.get(fork); assert.equal(fs.pending, null); assert.equal(fs.active?.content.instructions, "LEGACY");
		await t.chat.abort(ctx); await submission.wait(ctx);
		await (await t.profiles.submit(t.chat, "next")).wait(ctx);
		assert.match(JSON.stringify(t.serve.bodies[1].messages), /NEXT/);
		const active = await t.profiles.get(t.chat);
		const restored = await t.profiles.restore(t.chat, active.version, atPending);
		assert.equal(restored.active?.content.instructions, "LEGACY"); assert.ok(restored.active!.revision > active.active!.revision);
		assert.equal((await t.harness.snapshot(AgentDoc, t.chat.id, ctx))?.instructions, "LEGACY");
		await assert.rejects(t.profiles.restore(t.chat, restored.version, 999999 as EntryId));
	} finally { await t.close(); }
});
test("profiles: restart retains the applied and pending revisions without a model call", async () => {
	const storage = testStorage(); let t = await setup(storage, true);
	let id = t.chat.id;
	try {
		let s = await t.profiles.get(t.chat);
		s = await t.profiles.save(t.chat, s.version, { ...emptyContent("PERSIST"), skills: [skill] });
		await t.host.setReadout(t.chat, CONFIG);
		const live = await t.profiles.submit(t.chat, "hold"); await waitFor(() => t.serve.bodies.length === 1);
		s = await t.profiles.save(t.chat, s.version, emptyContent("PENDING_AFTER_RESTART"));
		await t.chat.abort(ctx); await live.wait(ctx);
		await t.close(); t = await setup(storage);
		assert.equal(t.chat.id, id);
		assert.deepEqual(await t.profiles.get(t.chat), s); assert.equal(t.serve.bodies.length, 0);
		await (await t.profiles.submit(t.chat, "activate")).wait(ctx);
		assert.match(JSON.stringify(t.serve.bodies[0].messages), /PENDING_AFTER_RESTART/);
	} finally { await t.close(); }
});
test("profiles: simultaneous saves at one version have one winner", async () => {
	const t = await setup(); try {
		const s = await t.profiles.get(t.chat);
		const r = await Promise.allSettled(["A", "B"].map(v => t.profiles.save(t.chat, s.version, emptyContent(v))));
		assert.equal(r.filter(x => x.status === "fulfilled").length, 1);
	} finally { await t.close(); }
});
test("profiles: imported provenance survives manual edits and is marked as edited", async () => {
	const t = await setup(); try {
		const state = await t.profiles.get(t.chat);
		const origin = { source: "/operator/harness", digest: "d".repeat(64), files: [{ path: "systemprompt.md", sha256: "e".repeat(64), disposition: "instructions" }], delivery: "on-demand to inline", reference: "Current profile", referenceDigest: "f".repeat(64), edited: false };
		const imported = await t.profiles.save(t.chat, state.version, emptyContent("IMPORTED"), origin);
		const edited = await t.profiles.save(t.chat, imported.version, emptyContent("EDITED"));
		assert.deepEqual(edited.active?.origin, { ...origin, edited: true });
	} finally { await t.close(); }
});
