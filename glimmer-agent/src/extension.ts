/**
 * The readout extension (spec section 4; rulings P3-R2, P3-R4, P3-R6 and P3-R8, and the 2026-10-24 review rulings R1,
 * R2, R3 and the Pi persistence lifecycle).
 * - Settings are rewindable conversation documents, so a fork or a restore brings back the settings of its point:
 *   glimmer.readout.config and glimmer.steer, each with the settings version the bridge orders changes by.
 * - glimmer.readout.state holds the lineage's turn count, its recording continuity, and the request in flight.
 * - beforeRequest joins the previous request's finalization, settles a request that did not finish in this lineage,
 *   writes the new request's identity ahead of sending it, and registers its nomos block and record sink.
 * - The sink takes every SSE chunk through the provider's awaited onProviderStreamEvent: a glimmer.readout.batch entry
 *   every BATCH records, then one glimmer.readout.turn record with its terminal status, appended in the transaction that
 *   advances glimmer.readout.state. afterResponse joins that finalization before Pi commits the answer.
 * - The stock stream's terminal message decides that status: a summary is only a candidate until the stream ends
 *   successfully (review F1). Until a provider call takes the request, the generation's signal owns it (review F2).
 * - close() owns admission (ruling A10): once closing begins it admits no preparation, joins the ones it admitted,
 *   and records their unsent requests before it disables terminal writes.
 * - Readout entries carry no model messages, so they never reach the model.
 */
import { randomUUID } from "node:crypto";
import type { AssistantMessage, Message } from "@earendil-works/pi-ai";
import { type NomosBlock, type ReadoutHeader, type ReadoutRecord, type ReadoutSummary, TurnCheck, type TurnUpdate } from "./contract.ts";
import {
	BACKGROUND_CONTEXT,
	type Context,
	type Conversation,
	type ConversationId,
	type Cursor,
	defineDoc,
	defineEntry,
	defineExtension,
	type EntryId,
	GenerationTask,
	type HarnessT,
	hook,
	type HookApi,
	ProviderDoc,
} from "./pi.ts";
import { type ReadoutRequest, ReadoutRequests, type ReadoutSink } from "./provider.ts";

export const BATCH = 16;

export type ReadoutConfig = {
	enabled: boolean;
	emotion: { layers: number[]; directions: string } | null;
	workspace: { layers: number[]; lens: string; k: number } | null;
};
export type Steer = { layer: number; directions: string; direction: string; strength: number };
/** glimmer.readout.config: the readout settings, and the settings version of their last change. */
export type ReadoutConfigState = {
	enabled: boolean;
	emotion: { layers: number[]; directions: string } | null;
	workspace: { layers: number[]; lens: string; k: number } | null;
	version: number;
};
/** glimmer.steer: the steering for the next reply, and the settings version of its last change. */
export type SteerConfig = { steer: Steer | null; version: number };
/** The lineage's last turn was fully recorded, in this conversation, under these readout settings (ruling R3). */
export type Continuity = { conversation: string; key: string };
/** A request written ahead of its send, until its turn record lands (the persistence lifecycle). */
export type OpenRequest = { request: string; conversation: string; turn: number; task: number; recorded: boolean; since: number | null };
/**
 * The lineage's last settled request: its turn record's entry, and the entry its batches follow. A fork taken inside
 * that request's final transaction inherits this without the turn record, which is how prepare finds it (ruling A8).
 */
export type ClosedRequest = { request: string; turn: number; task: number; recorded: boolean; since: number | null; entry: number };
export type ReadoutState = { turns: number; continuity: Continuity | null; open: OpenRequest | null; last: ClosedRequest | null };
export type TurnStatus = "complete" | "aborted" | "transport_error" | "contract_error";
export type ReadoutBatch = { request: string; turn: number; header: ReadoutHeader; records: ReadoutRecord[] };
export type ReadoutTurnRecord = {
	/** The request's durable identity: its write-ahead mark, every batch, and this record carry it. */
	request: string;
	/** The generation task that sent it. */
	task: number;
	/** The block's `turn`: this lineage's count of turn records before this one. */
	turn: number;
	status: TurnStatus;
	/** The request asked for readouts; false for a steering-only request (ruling R2). */
	recorded: boolean;
	/** The turn header, or null when none arrived. */
	header: ReadoutHeader | null;
	/** The serve's summary: on complete turns only, never invented. */
	summary: ReadoutSummary | null;
	/** Every record received, in position order; on a complete turn, exactly the summary's ranges (ruling R5). */
	records: ReadoutRecord[];
	/** Why the turn is not complete. */
	error: string | null;
};
export type SettingsMessage = { type: "settings"; conversationId: ConversationId; version: number; readout: ReadoutConfig; steer: Steer | null };
/** A failure the host could not record durably: the bridge forwards it. */
export type ReadoutErrorMessage = { type: "readout.error"; conversationId: ConversationId; request: string | null; error: string };

const OFF: ReadoutConfig = { enabled: false, emotion: null, workspace: null };

export const ReadoutConfigDoc = defineDoc<ReadoutConfigState>({
	kind: "glimmer.readout.config",
	version: 1,
	scope: "conversation",
	history: "rewindable",
	fork: "asOf",
	initial: () => ({ enabled: false, emotion: null, workspace: null, version: 0 }),
});
export const SteerDoc = defineDoc<SteerConfig>({
	kind: "glimmer.steer",
	version: 1,
	scope: "conversation",
	history: "rewindable",
	fork: "asOf",
	initial: () => ({ steer: null, version: 0 }),
});
export const ReadoutStateDoc = defineDoc<ReadoutState>({
	kind: "glimmer.readout.state",
	version: 1,
	scope: "conversation",
	history: "rewindable",
	fork: "asOf",
	initial: () => ({ turns: 0, continuity: null, open: null, last: null }),
});
export const BatchEntry = defineEntry<ReadoutBatch>("glimmer.readout.batch");
export const TurnEntry = defineEntry<ReadoutTurnRecord>("glimmer.readout.turn");

/** The settings a recorded turn continues from: layers and asset IDs, in a fixed order. */
function settingsKey(emotion: { layers: number[]; directions: string }, workspace: ReadoutConfig["workspace"]): string {
	return JSON.stringify([emotion.layers, emotion.directions, workspace?.layers ?? null, workspace?.lens ?? null, workspace?.k ?? null]);
}

function errorText(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/** How the stock stream's terminal message ends a turn; "success" still needs the summary (review F1). */
function endingOf(message: AssistantMessage): "success" | "aborted" | "transport_error" {
	if (message.stopReason === "stop" || message.stopReason === "length" || message.stopReason === "toolUse") return "success";
	return message.stopReason === "aborted" ? "aborted" : "transport_error";
}

/** One request's readouts, from its first SSE chunk to its durable terminal record (the persistence lifecycle). */
class TurnSink implements ReadoutSink {
	readonly request: string;
	readonly turn: number;
	readonly recorded: boolean;
	/** Settles when the terminal record is committed, or its failure is reported. Never rejects. */
	readonly done: Promise<void>;
	readonly #host: ReadoutHost;
	readonly #conversation: Conversation;
	readonly #task: number;
	readonly #since: number | null;
	readonly #key: string | null;
	readonly #check: TurnCheck;
	readonly #records: ReadoutRecord[] = [];
	#header: ReadoutHeader | null = null;
	#pending: ReadoutRecord[] = [];
	#writable = true;
	#finishing: Promise<void> | undefined;
	#settle: () => void = () => undefined;

	constructor(host: ReadoutHost, conversation: Conversation, open: OpenRequest, block: NomosBlock, key: string | null) {
		this.#host = host;
		this.#conversation = conversation;
		this.request = open.request;
		this.turn = open.turn;
		this.recorded = open.recorded;
		this.#task = open.task;
		this.#since = open.since;
		this.#key = key;
		this.#check = new TurnCheck(block);
		this.done = new Promise<void>((resolve) => {
			this.#settle = () => resolve();
		});
	}

	/** The finalization in flight, or a settled promise before one starts. */
	get finishing(): Promise<void> {
		return this.#finishing ?? Promise.resolve();
	}

	/** Check and keep one chunk. A summary is kept by the check as a candidate; it ends nothing (review F1). */
	async chunk(data: unknown): Promise<void> {
		if (this.#finishing !== undefined) return; // a contract error already ended the turn
		let update: TurnUpdate | undefined;
		try {
			update = this.#check.take(data);
		} catch (error) {
			await this.#finish("contract_error", errorText(error), null);
			return;
		}
		if (update === undefined) return;
		if (update.header !== null) this.#header = update.header;
		this.#records.push(...update.records);
		this.#pending.push(...update.records);
		while (this.#pending.length >= BATCH) await this.#writeBatch(this.#pending.splice(0, BATCH));
	}

	/**
	 * The stock stream's terminal message ends the turn (review F1). It is complete only when the stream ended successfully
	 * and the check hands out the summary for the completion the message names; an abort or a transport error after the
	 * summary wins. A contract error that came first stays.
	 */
	end(message: AssistantMessage): void {
		const ending = endingOf(message);
		if (ending !== "success") {
			void this.#finish(ending, message.errorMessage ?? `the reply ended (${message.stopReason})`, null);
			return;
		}
		let summary: ReadoutSummary;
		try {
			summary = this.#check.close(message.responseId);
		} catch (error) {
			void this.#finish("contract_error", errorText(error), null);
			return;
		}
		void this.#finish("complete", null, summary);
	}

	/** Settle a request the provider never sent. */
	abandon(status: TurnStatus, error: string): void {
		void this.#finish(status, error, null);
	}

	#finish(status: TurnStatus, error: string | null, summary: ReadoutSummary | null): Promise<void> {
		this.#finishing ??= this.#commitTurn(status, error, summary).finally(() => this.#settle());
		return this.#finishing;
	}

	async #writeBatch(records: ReadoutRecord[]): Promise<void> {
		const header = this.#header;
		if (!this.#writable || header === null || this.#host.closed) return;
		const id = this.#conversation.id;
		try {
			await this.#conversation.commit(async (tx) => {
				await tx.appendEntry(BatchEntry, id, { data: { request: this.request, turn: this.turn, header, records } });
			}, this.#host.context);
		} catch (error) {
			this.#writable = false; // the turn record still carries these records
			this.#host.report(id, this.request, error);
		}
	}

	/** One transaction: the unflushed records as batches, the turn record, and the lineage's readout state. */
	async #commitTurn(status: TurnStatus, error: string | null, summary: ReadoutSummary | null): Promise<void> {
		const id = this.#conversation.id;
		if (this.#host.closed) {
			this.#host.report(id, this.request, new Error(`request ${this.request} ended (${status}) after the host closed; the next start settles it`));
			return;
		}
		const header = this.#header;
		const pending = this.#pending;
		this.#pending = [];
		const turn: ReadoutTurnRecord = {
			request: this.request,
			task: this.#task,
			turn: this.turn,
			status,
			recorded: this.recorded,
			header: header ?? summary?.header ?? null,
			summary,
			records: [...this.#records],
			error,
		};
		const continuity = status === "complete" && this.#key !== null ? { conversation: String(id), key: this.#key } : null;
		try {
			await this.#conversation.commit(async (tx) => {
				if (header !== null) {
					for (let i = 0; i < pending.length; i += BATCH) {
						const records = pending.slice(i, i + BATCH);
						await tx.appendEntry(BatchEntry, id, { data: { request: this.request, turn: this.turn, header, records } });
					}
				}
				const entry = await tx.appendEntry(TurnEntry, id, { data: turn });
				const state = await tx.doc(ReadoutStateDoc, id);
				state.turns = Math.max(state.turns, this.turn + 1);
				state.continuity = continuity;
				if (state.open?.request === this.request) state.open = null;
				state.last = { request: this.request, turn: this.turn, task: this.#task, recorded: this.recorded, since: this.#since, entry: entry.id };
			}, this.#host.context);
		} catch (failure) {
			this.#host.report(id, this.request, failure);
		}
	}
}

export class ReadoutHost {
	readonly requests = new ReadoutRequests();
	/** Failures the host could not record durably, in order; listeners (the bridge) get each one too. */
	readonly errors: ReadoutErrorMessage[] = [];
	readonly #context: Context;
	readonly #active = new Map<ConversationId, { sessionId: string; sink: TurnSink }>();
	/** The preparations admitted before closing began, until each one ends (ruling A10). */
	readonly #preparing = new Set<Promise<void>>();
	readonly #settingsListeners = new Set<(m: SettingsMessage) => void>();
	readonly #errorListeners = new Set<(m: ReadoutErrorMessage) => void>();
	#harness: HarnessT | undefined;
	#closing = false;
	#closed = false;

	constructor(context: Context = BACKGROUND_CONTEXT) {
		this.#context = context;
	}

	get context(): Context {
		return this.#context;
	}

	get closed(): boolean {
		return this.#closed;
	}

	attach(harness: HarnessT): void {
		this.#harness = harness;
	}

	/**
	 * The GenerationTask's beforeRequest hook (ruling P3-R2 and the persistence lifecycle): join the previous request,
	 * settle one that did not finish in this lineage, write this request ahead of its send, and register it.
	 * Admission is decided here, synchronously (ruling A10): once closing began, a preparation is not admitted. Otherwise
	 * it is tracked until it ends, so a close() that begins while it awaits joins it.
	 */
	async prepare(api: HookApi, messages: readonly Message[], context: Context): Promise<void> {
		if (this.#closing) {
			await this.#unadmitted(api, context);
			return;
		}
		const preparation = this.#prepareAdmitted(api, messages, context);
		this.#preparing.add(preparation);
		try {
			await preparation;
		} finally {
			this.#preparing.delete(preparation);
		}
	}

	async #prepareAdmitted(api: HookApi, messages: readonly Message[], context: Context): Promise<void> {
		const id = api.conversationId;
		try {
			await this.#join(id, "aborted", "the attempt ended before its readout request was sent");
			const conversation = await this.#conversationOf(id);
			const config = await api.snapshot(ReadoutConfigDoc, id, context);
			const steer = (await api.snapshot(SteerDoc, id, context))?.steer ?? null;
			const stored = await api.snapshot(ReadoutStateDoc, id, context);
			const state = await this.#reconcile(conversation, stored ?? { turns: 0, continuity: null, open: null, last: null });
			const emotion = config !== undefined && config.enabled ? config.emotion : null;
			const workspace = emotion === null ? null : (config?.workspace ?? null);
			if (emotion === null && steer === null) {
				await this.#breakContinuity(conversation, state); // an unrecorded turn (ruling R3)
				return;
			}
			const provider = await api.snapshot(ProviderDoc, id, context);
			if (provider === undefined) {
				this.report(id, null, new Error("pi.provider is not written yet: this request goes without readouts or steering"));
				await this.#breakContinuity(conversation, state);
				return;
			}
			const key = emotion === null ? null : settingsKey(emotion, workspace);
			const intact = key !== null && state.continuity?.conversation === String(id) && state.continuity.key === key;
			const block: NomosBlock = { conversation: String(id), turn: state.turns }; // ruling R1: a decimal string
			if (emotion !== null) {
				block.readout = { emotion: { layers: [...emotion.layers], directions: emotion.directions }, prompt: intact ? "new" : "all" };
				if (workspace !== null) block.readout.workspace = { layers: [...workspace.layers], lens: workspace.lens, k: workspace.k };
			}
			if (steer !== null) block.steer = { layer: steer.layer, directions: steer.directions, direction: steer.direction, strength: steer.strength };
			const open: OpenRequest = { request: randomUUID(), conversation: String(id), turn: state.turns, task: api.taskId, recorded: emotion !== null, since: null };
			const since = await conversation.commit(async (tx) => {
				const newest = (await tx.scanEntries({ conversationId: id }, 1)).items[0];
				const mark = newest === undefined ? null : newest.id;
				const written = await tx.doc(ReadoutStateDoc, id);
				written.continuity = null; // a request in flight is not a recorded turn yet
				written.open = { ...open, since: mark };
				return mark;
			}, this.#context);
			const sink = new TurnSink(this, conversation, { ...open, since }, block, key);
			this.#active.set(id, { sessionId: provider.sessionId, sink });
			this.#own(provider.sessionId, sink, { block, sink, last: messages.at(-1) }, context.abortSignal);
		} catch (error) {
			this.report(id, null, error);
		}
	}

	/**
	 * Register a request and give it one owner until a provider call takes it (review F2): the generation's own signal,
	 * which fires when the run is aborted and when its invocation ends. If it fires first, the request is withdrawn and
	 * recorded as aborted. A call that took the request is owned by the stream's end instead, so exactly one ends it.
	 * A preparation admitted before closing began that gets here after it began registers nothing: close() is joining
	 * it, so its request is recorded as aborted now, while the host can still record it, and goes out without readouts or
	 * steering (ruling A10).
	 */
	#own(sessionId: string, sink: TurnSink, request: ReadoutRequest, signal: AbortSignal | undefined): void {
		if (this.#closing) {
			sink.abandon("aborted", "the host closed before its readout request was sent");
			return;
		}
		this.requests.register(sessionId, request);
		if (signal === undefined) return;
		const unsent = (): void => {
			if (this.requests.withdraw(sessionId, sink)) {
				sink.abandon("aborted", `the generation ended before its readout request was sent: ${errorText(signal.reason)}`);
			}
		};
		if (signal.aborted) {
			unsent();
			return;
		}
		signal.addEventListener("abort", unsent, { once: true });
		void sink.done.then(() => signal.removeEventListener("abort", unsent));
	}

	/** The GenerationTask's afterResponse hook: the request's turn record is durable before Pi commits the answer. */
	async afterResponse(api: HookApi, message: AssistantMessage): Promise<void> {
		const status: TurnStatus = message.stopReason === "aborted" ? "aborted" : "transport_error";
		await this.#join(api.conversationId, status, `the readout request was never sent: ${message.errorMessage ?? message.stopReason}`);
	}

	/** Resolves once the conversation's current request has its terminal record, or its failure is reported. */
	async settled(conversationId: ConversationId): Promise<void> {
		await this.#active.get(conversationId)?.sink.done;
	}

	/**
	 * Shut down (review F2; ruling A10, close() owns admission).
	 * - From its first step no preparation is admitted, and it joins every preparation admitted before: each one records
	 *   its request as aborted if it had not registered it yet.
	 * - A registered request no provider call has taken yet is withdrawn and recorded as aborted.
	 * - Only then are terminal writes disabled. Every finalization already started is joined, and no turn is recorded
	 *   after. A request still streaming is settled from its batches at the next start; abort or finish the
	 *   conversations first to record it now.
	 * It needs no quiescence first, and a second call is harmless.
	 */
	async close(): Promise<void> {
		this.#closing = true; // synchronously: a preparation that starts after this is not admitted
		await Promise.allSettled([...this.#preparing]);
		for (const active of this.#active.values()) {
			if (this.requests.withdraw(active.sessionId, active.sink)) {
				active.sink.abandon("aborted", "the host closed before its readout request was sent");
			}
		}
		this.#closed = true; // the abandoned requests above started their commits while the host was open
		await Promise.all([...this.#active.values()].map((active) => active.sink.finishing));
	}

	async setReadout(conversation: Conversation, cfg: ReadoutConfig): Promise<void> {
		await conversation.commit(async (tx) => {
			const config = await tx.doc(ReadoutConfigDoc, conversation.id);
			const steer = await tx.doc(SteerDoc, conversation.id);
			config.enabled = cfg.enabled;
			config.emotion = structuredClone(cfg.emotion);
			config.workspace = structuredClone(cfg.workspace);
			config.version = Math.max(config.version, steer.version) + 1;
		}, this.#context);
		await this.#announce(conversation.id);
	}

	async setSteer(conversation: Conversation, steer: Steer | null): Promise<void> {
		await conversation.commit(async (tx) => {
			const config = await tx.doc(ReadoutConfigDoc, conversation.id);
			const doc = await tx.doc(SteerDoc, conversation.id);
			doc.steer = structuredClone(steer);
			doc.version = Math.max(config.version, doc.version) + 1;
		}, this.#context);
		await this.#announce(conversation.id);
	}

	/** The conversation's current settings and their version (ruling R10, #13): what a bridge starts from. */
	async settings(conversationId: ConversationId): Promise<SettingsMessage> {
		const harness = this.#need();
		for (;;) {
			const config = await harness.snapshot(ReadoutConfigDoc, conversationId, this.#context);
			const steer = await harness.snapshot(SteerDoc, conversationId, this.#context);
			const again = await harness.snapshot(ReadoutConfigDoc, conversationId, this.#context);
			if ((again?.version ?? 0) !== (config?.version ?? 0)) continue; // a change landed between the reads
			return {
				type: "settings",
				conversationId,
				version: Math.max(config?.version ?? 0, steer?.version ?? 0),
				readout:
					config === undefined
						? structuredClone(OFF)
						: { enabled: config.enabled, emotion: structuredClone(config.emotion), workspace: structuredClone(config.workspace) },
				steer: structuredClone(steer?.steer ?? null),
			};
		}
	}

	onSettings(listener: (m: SettingsMessage) => void): () => void {
		this.#settingsListeners.add(listener);
		return () => {
			this.#settingsListeners.delete(listener);
		};
	}

	onError(listener: (m: ReadoutErrorMessage) => void): () => void {
		this.#errorListeners.add(listener);
		return () => {
			this.#errorListeners.delete(listener);
		};
	}

	report(conversationId: ConversationId, request: string | null, error: unknown): void {
		const message: ReadoutErrorMessage = { type: "readout.error", conversationId, request, error: errorText(error) };
		this.errors.push(message);
		for (const listener of this.#errorListeners) listener(message);
	}

	/** Join the conversation's request: settle it as `status` if the provider never took it, then await its record. */
	async #join(conversationId: ConversationId, status: TurnStatus, error: string): Promise<void> {
		const active = this.#active.get(conversationId);
		if (active === undefined) return;
		if (this.requests.withdraw(active.sessionId, active.sink)) active.sink.abandon(status, error);
		await active.sink.done;
		if (this.#active.get(conversationId) === active) this.#active.delete(conversationId);
	}

	/**
	 * A preparation that arrives once closing began (ruling A10). Its request goes without readouts or steering, so it is
	 * an unrecorded turn: after the previous request's finalization, it breaks the lineage's recording continuity (ruling
	 * R3). It writes nothing else; the next start settles anything left open. A request that asked for readouts or
	 * steering is reported.
	 */
	async #unadmitted(api: HookApi, context: Context): Promise<void> {
		const id = api.conversationId;
		try {
			await this.#join(id, "aborted", "the attempt ended before its readout request was sent");
			const config = await api.snapshot(ReadoutConfigDoc, id, context);
			const steer = (await api.snapshot(SteerDoc, id, context))?.steer ?? null;
			if ((config?.enabled === true && config.emotion !== null) || steer !== null) {
				this.report(id, null, new Error("the readout host is closing: this request goes without readouts or steering"));
			}
			const stored = await api.snapshot(ReadoutStateDoc, id, context);
			if (stored !== undefined) await this.#breakContinuity(await this.#conversationOf(id), stored);
		} catch (error) {
			this.report(id, null, error);
		}
	}

	/**
	 * Settle a request that did not finish in this lineage, as aborted, from the batches it left (the persistence
	 * lifecycle; ruling A8). Forks stay allowed at any cursor:
	 * - `open` names a request a restart cut, whose finalization failed, or that ran when a fork was taken;
	 * - a fork taken inside a request's final transaction inherits the closed state, `last`, without the turn record that
	 *   committed with it. That request's visible batches then have no visible terminal entry, so it is settled too.
	 * A lineage whose last turn record is visible, such as a fork at that record, is returned unchanged.
	 */
	async #reconcile(conversation: Conversation, state: ReadoutState): Promise<ReadoutState> {
		const id = conversation.id;
		let unsettled: Omit<ClosedRequest, "entry"> | null = state.open;
		if (unsettled === null && state.last !== null) {
			const at = state.last.entry as EntryId;
			const seen = await conversation.entries({ minEntryId: at, maxEntryId: at, order: "ascending" }, 1, undefined, this.#context);
			if (seen.items[0]?.id !== at) unsettled = state.last;
		}
		if (unsettled === null) return state;
		const orphan = unsettled;
		return conversation.commit(async (tx) => {
			const batches: ReadoutBatch[] = [];
			let cursor: Cursor | undefined;
			do {
				const from = orphan.since === null ? {} : { minEntryId: orphan.since as EntryId };
				const page = await tx.scanEntries({ conversationId: id, ...from, order: "ascending" }, 256, cursor);
				for (const entry of page.items) {
					if (BatchEntry.is(entry) && entry.data.request === orphan.request) batches.push(entry.data);
				}
				cursor = page.next;
			} while (cursor !== undefined);
			const turn: ReadoutTurnRecord = {
				request: orphan.request,
				task: orphan.task,
				turn: orphan.turn,
				status: "aborted",
				recorded: orphan.recorded,
				header: batches[0]?.header ?? null,
				summary: null,
				records: batches.flatMap((b) => b.records),
				error: `request ${orphan.request} did not finish in conversation ${id}; its records are the batches it left`,
			};
			const entry = await tx.appendEntry(TurnEntry, id, { data: turn });
			const last: ClosedRequest = {
				request: orphan.request,
				turn: orphan.turn,
				task: orphan.task,
				recorded: orphan.recorded,
				since: orphan.since,
				entry: entry.id,
			};
			const written = await tx.doc(ReadoutStateDoc, id);
			written.turns = Math.max(written.turns, orphan.turn + 1);
			written.continuity = null;
			written.open = null;
			written.last = last;
			return { turns: written.turns, continuity: null, open: null, last };
		}, this.#context);
	}

	async #breakContinuity(conversation: Conversation, state: ReadoutState): Promise<void> {
		if (state.continuity === null) return;
		await conversation.commit(async (tx) => {
			(await tx.doc(ReadoutStateDoc, conversation.id)).continuity = null;
		}, this.#context);
	}

	async #announce(conversationId: ConversationId): Promise<void> {
		const message = await this.settings(conversationId);
		for (const listener of this.#settingsListeners) listener(message);
	}

	async #conversationOf(id: ConversationId): Promise<Conversation> {
		const conversation = await this.#need().conversation(id, this.#context);
		if (conversation === undefined) throw new Error(`no conversation ${id}`);
		return conversation;
	}

	#need(): HarnessT {
		if (this.#harness === undefined) throw new Error("ReadoutHost.attach(harness) was not called");
		return this.#harness;
	}
}

export function readoutExtension(host: ReadoutHost) {
	return defineExtension({
		name: "glimmer-readout",
		hooks: [
			hook(GenerationTask, {
				beforeRequest: async (request, api, context) => {
					await host.prepare(api, request.messages, context);
					return undefined; // the request's messages are unchanged
				},
				afterResponse: async (message, api) => {
					await host.afterResponse(api, message);
				},
			}),
		],
	});
}
