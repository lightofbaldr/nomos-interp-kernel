/** Durable, text-only profiles. All app submissions/edits go through this host's admission lock. */
import { createHash } from "node:crypto";
import {
	AgentDoc, configure, defineDoc, defineEntry, defineExtension, GenerationTask, hook, InboxDoc, LiveDoc, section,
	type Context, type Conversation, type ConversationId, type EntryId, type HarnessT, type HookApi, type Tx,
} from "./pi.ts";
import { ReadoutStateDoc } from "./extension.ts";
import { emptyContent, PROFILE_POLICY, profileSections, validateContent, type ProfileContent, type ProfileOrigin, type ProfileRevision, type ProfileState, type ProfileUsed } from "./profile-model.ts";

export const ProfileDoc = defineDoc<ProfileState>({ kind: "glimmer.profile", version: 1, scope: "conversation", history: "rewindable", fork: "asOf", initial: () => ({ owner: 0, version: 0, active: null, pending: null }) });
export const ProfileChanged = defineEntry<{ action: string; version: number; active: ProfileRevision; pending: ProfileRevision | null }>("glimmer.profile.changed");
export const ProfileUsedEntry = defineEntry<ProfileUsed>("glimmer.profile.used");
// Dedup is per conversation, not inherited into a fork. Recovery of an already-attributed request is safe.
const RequestDoc = defineDoc<{ key: string }>({ kind: "glimmer.profile.request", version: 1, scope: "conversation", history: "rewindable", fork: "initial", initial: () => ({ key: "" }) });
export function profileDigest(content: ProfileContent) { return createHash("sha256").update(JSON.stringify([PROFILE_POLICY, content])).digest("hex"); }
function revision(n: number, content: ProfileContent, origin: ProfileOrigin | null): ProfileRevision {
	// Origin may be read from a Chord transaction draft; copy its data, never structuredClone its proxies.
	return { revision: n, digest: profileDigest(content), policy: PROFILE_POLICY, content: structuredClone(content),
		origin: origin && { ...origin, files: origin.files.map(f => ({ ...f })) } };
}
async function busy(tx: Tx, id: ConversationId) {
	const live = await tx.doc(LiveDoc, id), inbox = await tx.doc(InboxDoc, id);
	return Boolean(live.run || live.compactions?.length || inbox.items.length);
}
async function activate(tx: Tx, id: ConversationId, state: ProfileState, p: ProfileRevision, action: string) {
	await configure(tx, id, { instructions: p.content.instructions, tools: [] });
	state.active = p; state.pending = null;
	await tx.appendEntry(ProfileChanged, id, { data: { action, version: state.version, active: p, pending: null } });
}

export class ProfileHost {
	#harness!: HarnessT;
	#context: Context;
	#locks = new Map<number, Promise<void>>();
	constructor(context: Context) { this.#context = context; }
	attach(harness: HarnessT) { this.#harness = harness; }
	async #locked<T>(id: number, work: () => Promise<T>): Promise<T> {
		const previous = this.#locks.get(id) ?? Promise.resolve();
		let release!: () => void; const current = new Promise<void>(r => { release = r; });
		this.#locks.set(id, current); await previous;
		try { return await work(); } finally { release(); if (this.#locks.get(id) === current) this.#locks.delete(id); }
	}
	async #ensure(c: Conversation) {
		await c.commit(async tx => {
			const state = await tx.doc(ProfileDoc, c.id), native = await tx.doc(AgentDoc, c.id);
			if (state.owner === c.id && state.active) return;
			if (await busy(tx, c.id)) throw Error("Wait for saved work to settle before initializing this profile.");
			if (state.active) {
				// Pi copied both rewindable documents as-of the fork point. Pending edits never follow a fork.
				if (native.instructions !== state.active.content.instructions) throw Error("Fork profile disagrees with Pi instructions.");
				state.owner = c.id; state.pending = null; state.version++;
				await activate(tx, c.id, state, state.active, "fork");
			} else {
				if (native.tools && (!Array.isArray(native.tools) || native.tools.length)) throw Error("This text-only profile cannot adopt a tool-enabled conversation.");
				const content = emptyContent(native.instructions ?? "");
				// Preserve legacy instructions. Larger legacy values still need the explicit size contract.
				content.allowLarge = true; validateContent(content); content.allowLarge = content.instructions.length > 5900;
				state.owner = c.id; state.version = 1;
				await activate(tx, c.id, state, revision(1, content, null), "initialize");
			}
		}, this.#context);
	}
	async get(c: Conversation): Promise<ProfileState> {
		return this.#locked(c.id, async () => { await this.#ensure(c); return structuredClone((await this.#harness.snapshot(ProfileDoc, c.id, this.#context))!); });
	}
	async #save(c: Conversation, expected: number, content: ProfileContent, origin: ProfileOrigin | null | undefined, action: string) {
		await this.#ensure(c);
		await c.commit(async tx => {
			const state = await tx.doc(ProfileDoc, c.id);
			if (!Number.isSafeInteger(expected) || expected !== state.version) throw Error("Profile changed. Reload before saving; your draft was not applied.");
			const isBusy = await busy(tx, c.id);
			const source = origin === undefined ? state.pending?.origin ?? state.active?.origin ?? null : origin;
			const p = revision(++state.version, content, source && origin === undefined ? { ...source, edited: true } : source);
			if (isBusy) {
				state.pending = p;
				await tx.appendEntry(ProfileChanged, c.id, { data: { action: "pending", version: state.version, active: state.active!, pending: p } });
			} else await activate(tx, c.id, state, p, action);
		}, this.#context);
		return structuredClone((await this.#harness.snapshot(ProfileDoc, c.id, this.#context))!);
	}
	/** origin is trusted server-side import metadata, never accepted from an HTTP client. */
	async save(c: Conversation, expected: number, value: unknown, origin?: ProfileOrigin | null) {
		const content = validateContent(value);
		return this.#locked(c.id, () => this.#save(c, expected, content, origin, origin ? "import" : "save"));
	}
	async restore(c: Conversation, expected: number, at: EntryId) {
		return this.#locked(c.id, async () => {
			const old = await this.#harness.snapshotAsOf(ProfileDoc, c.id, at, this.#context);
			if (!old?.active) throw Error("No recorded profile at this entry.");
			return this.#save(c, expected, validateContent(old.active.content), old.active.origin, "restore");
		});
	}
	async fork(c: Conversation, at: EntryId) {
		return this.#locked(c.id, async () => {
			const child = await c.fork(at, { ownership: { kind: "ownerless" } }, this.#context);
			await this.#ensure(child); return child;
		});
	}
	async submit(c: Conversation, content: string) {
		return this.#locked(c.id, async () => {
			await this.#ensure(c);
			await c.commit(async tx => {
				if (await busy(tx, c.id)) throw Error("Conversation still has a model/tool run or queued input.");
				const state = await tx.doc(ProfileDoc, c.id);
				if (state.pending) { state.version++; await activate(tx, c.id, state, state.pending, "activate-pending"); }
			}, this.#context);
			return c.submit({ type: "input", content, whenBusy: "reject" }, this.#context);
		});
	}
	async used(api: HookApi) {
		const task = await this.#harness.getTask(api.taskId, this.#context), checkpoint = task?.state.checkpoint;
		if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint) || checkpoint.phase !== "request" || typeof checkpoint.cutoff !== "number" || typeof checkpoint.attempt !== "number") throw Error("Cannot attribute this Pi request checkpoint.");
		const state = await this.#harness.snapshotAsOf(ProfileDoc, api.conversationId, checkpoint.cutoff as EntryId, this.#context);
		if (!state?.active) return; // Legacy/unmanaged turn: explicitly unrecorded, never today's profile.
		const native = await this.#harness.snapshotAsOf(AgentDoc, api.conversationId, checkpoint.cutoff as EntryId, this.#context);
		if (native?.instructions !== state.active.content.instructions || !Array.isArray(native.tools) || native.tools.length) throw Error("Profile attribution disagrees with Pi's prepared agent.");
		const readout = await api.snapshot(ReadoutStateDoc, api.conversationId, this.#context);
		const request = readout?.open?.task === api.taskId ? readout.open.request : null;
		const key = JSON.stringify([api.taskId, checkpoint.attempt, checkpoint.cutoff, request]);
		const c = await this.#harness.conversation(api.conversationId, this.#context);
		if (!c) throw Error("Missing profile conversation.");
		await c.commit(async tx => {
			const last = await tx.doc(RequestDoc, c.id);
			if (last.key === key) return;
			last.key = key;
			await tx.appendEntry(ProfileUsedEntry, c.id, { data: { task: api.taskId, attempt: checkpoint.attempt as number, cutoff: checkpoint.cutoff as number, request, profile: structuredClone(state.active!) } });
		}, this.#context);
	}
}

export function profileExtension(host: ProfileHost) {
	return defineExtension({ name: "glimmer-profile", sections: [section("glimmer-skills", async (input, ctx) => {
		const state = await input.read.snapshot(ProfileDoc, input.conversationId, ctx);
		if (!state?.active) return undefined;
		if (input.agent.instructions !== state.active.content.instructions || input.agent.tools.length) throw Error("Profile/agent drift refused.");
		return profileSections(state.active.content) || undefined;
	}, { tag: false })], hooks: [hook(GenerationTask, { beforeRequest: async (_request, api) => { await host.used(api); return undefined; } })] });
}
