import assert from "node:assert/strict";
import { test } from "node:test";
import { fileToolsExtension } from "../src/file-tools.ts";
import { emptyContent, SANDBOX_TOOLS, validateContent } from "../src/profile-model.ts";
import { ProfileHost, profileExtension } from "../src/profile.ts";
import { AgentDoc } from "../src/pi.ts";
import { AGENT, entries, openAgent } from "./harness.ts";
import { ctx, fakeServe, chunk, waitFor } from "./support.ts";

test("file catalog: fixed schemas, sequential calls, mutation replay unsafe; no shell or arbitrary tool names", () => {
  const ext = fileToolsExtension({ ready: async () => {}, execute: async () => ({ ok: true, text: "" }) });
  assert.deepEqual(ext.tools!.map(t => t.name), SANDBOX_TOOLS);
  for (const t of ext.tools!) {
    assert.equal(t.executionMode, "sequential");
    assert.equal(t.replay, t.name === "write" || t.name === "edit" || t.name === "mojo" ? "unsafe" : "safe");
    assert.deepEqual(t.callers, ["model"]);
    assert.equal((t.parameters as { additionalProperties?: boolean }).additionalProperties, false);
  }
  assert.throws(() => validateContent({ ...emptyContent(), tools: ["bash"] }), /Unknown/);
  assert.throws(() => validateContent({ ...emptyContent(), tools: ["read", "read"] }), /duplicate/);
  assert.deepEqual(validateContent({ ...emptyContent(), tools: ["write", "read"] }).tools, ["read", "write"]);
});

test("held tool run keeps its native capabilities through the second round; pending removal activates only on the next user input", async () => {
  let release!: () => void, started = false;
  const hold = new Promise<void>(r => { release = r; });
  const calls: string[] = [];
  const files = fileToolsExtension({ ready: async () => {}, execute: async (_id, name) => {
    calls.push(name); if (name === "write") { started = true; await hold; } return { ok: true, text: "SANDBOX_RESULT" };
  } });
  const serve = await fakeServe((_b, i) => ({ chunks: i < 2 ? [chunk(`r${i}`, { tool_calls: [{ index: 0, id: `call${i}`, type: "function", function: { name: i === 0 ? "write" : "read", arguments: JSON.stringify(i === 0 ? { path: "a.txt", content: "one" } : { path: "a.txt" }) } }] }), chunk(`r${i}`, {}, "tool_calls")] : [chunk(`r${i}`, { content: "Done" }), chunk(`r${i}`, {}, "stop")] }));
  const profiles = new ProfileHost(ctx, { ready: async () => {}, tools: files.tools! });
  const t = await openAgent(serve.url, undefined, [files, profileExtension(profiles)]); profiles.attach(t.harness);
  try {
    const chat = await t.harness.root(ctx, { ...AGENT, agent: { ...AGENT.agent, tools: [] } });
    const initial = await profiles.get(chat);
    const saved = await profiles.save(chat, initial.version, { ...emptyContent("Use files"), tools: ["read", "write"] });
    const submission = await profiles.submit(chat, "work"); await waitFor(() => started);
    const pending = await profiles.save(chat, saved.version, emptyContent("No files"));
    assert.deepEqual((await t.harness.snapshot(AgentDoc, chat.id, ctx))?.tools, ["read", "write"]);
    assert.deepEqual(pending.pending?.content.tools, []);
    release(); assert.equal((await submission.wait(ctx)).status, "done");
    assert.deepEqual(calls, ["write", "read"]);
    assert.equal(serve.bodies.length, 3);
    assert.ok(serve.bodies.slice(0, 3).every(b => b.tools.length === 2));
    const all = await entries(chat);
    assert.equal(all.filter(e => e.kind === "pi.tool-result").length, 2);
    assert.ok(all.filter(e => e.kind === "glimmer.profile.used").every(e => (e.data as any).profile.digest === saved.active?.digest));
    await (await profiles.submit(chat, "next")).wait(ctx);
    assert.equal(serve.bodies[3].tools?.length ?? 0, 0);
  } finally { release(); await t.host.close(); await t.harness.close(ctx); await serve.close(); }
});

test("text chat still works when isolation is missing; enabling tools fails before configuration", async () => {
  const serve = await fakeServe(() => ({ chunks: [chunk("r", { content: "text" }), chunk("r", {}, "stop")] }));
  const profiles = new ProfileHost(ctx), t = await openAgent(serve.url, undefined, [profileExtension(profiles)]); profiles.attach(t.harness);
  try {
    const chat = await t.harness.root(ctx, { ...AGENT, agent: { ...AGENT.agent, tools: [] } });
    const s = await profiles.get(chat);
    await assert.rejects(profiles.save(chat, s.version, { ...emptyContent(), tools: ["read"] }), /unavailable/);
    assert.deepEqual(await profiles.get(chat), s);
    await (await profiles.submit(chat, "text only")).wait(ctx);
    assert.equal(serve.bodies[0].tools?.length ?? 0, 0);
  } finally { await t.host.close(); await t.harness.close(ctx); await serve.close(); }
});

test("mojo is explicit, path-only and unsafe to replay; the broker receives the Pi conversation and cancellation signal", async () => {
  const seen: unknown[] = [], signal = new AbortController().signal;
  const ext = fileToolsExtension({ ready: async () => {}, execute: async (...args) => {
    seen.push(args); return { ok: false, text: "compile failed" };
  } });
  const tool = ext.tools!.find(t => t.name === "mojo")!;
  assert.equal(tool.replay, "unsafe");
  assert.deepEqual(Object.keys((tool.parameters as any).properties), ["path"]);
  assert.equal((tool.parameters as any).additionalProperties, false);
  assert.deepEqual(emptyContent().tools, []);
  const r = await tool.execute!({ path: "main.mojo" }, { conversationId: 12 } as any, { abortSignal: signal } as any);
  assert.deepEqual(seen, [[12, "mojo", { path: "main.mojo" }, signal]]);
  assert.equal((r as any).isError, true);
});

test("Mojo readiness is checked for the chosen tools before profile activation; file-only profiles do not require a compiler", async () => {
  const selected: unknown[] = [];
  const files = fileToolsExtension({ ready: async () => {}, execute: async () => ({ ok: true, text: "" }) });
  const serve = await fakeServe(() => ({ chunks: [chunk("r", { content: "text" }), chunk("r", {}, "stop")] }));
  const profiles = new ProfileHost(ctx, { tools: files.tools!, ready: async tools => {
    selected.push(tools); if (tools?.includes("mojo")) throw Error("compiler unavailable");
  } });
  const t = await openAgent(serve.url, undefined, [files, profileExtension(profiles)]); profiles.attach(t.harness);
  try {
    const chat = await t.harness.root(ctx, { ...AGENT, agent: { ...AGENT.agent, tools: [] } });
    const initial = await profiles.get(chat);
    await assert.rejects(profiles.save(chat, initial.version, { ...emptyContent(), tools: ["mojo"] }), /compiler unavailable/);
    assert.deepEqual(await profiles.get(chat), initial);
    await profiles.save(chat, initial.version, { ...emptyContent(), tools: ["read"] });
    assert.deepEqual(selected, [["mojo"], ["read"]]);
  } finally { await t.host.close(); await t.harness.close(ctx); await serve.close(); }
});
