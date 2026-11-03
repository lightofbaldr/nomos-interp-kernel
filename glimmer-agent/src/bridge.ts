/**
 * The UI and reports bridge (spec section 4; rulings P3-R5, R10 #13 and A9): one ordered stream per conversation.
 * - Pi's standard events (text, thinking, tools, and a `snapshot` after an overflow) come from watchEvents.
 * - Readout entries are recognized as they are appended: batches, and each turn's record with its terminal status.
 * - Settings changes come from the host's setters, and failures the host could not record from its error listeners.
 * - Attaching returns a snapshot: Pi's, and the conversation's current settings. The bridge subscribes to settings
 *   before it reads them and forwards only newer versions, so no change falls in the gap and none arrives twice.
 * - Every message goes out through one ordered chain, so a slow send holds the next one back (ruling A9). stop() joins
 *   the chain, and rejects with the first send that failed; delivery ends at that failure.
 * Nothing in the UI talks to the serve directly.
 */
import {
	BatchEntry,
	type ReadoutBatch,
	type ReadoutErrorMessage,
	type ReadoutHost,
	type ReadoutTurnRecord,
	type SettingsMessage,
	TurnEntry,
} from "./extension.ts";
import { type AgentEvent, type Context, type ConversationId, type EntryId, type HarnessT, type SnapshotEvent, watchEvents } from "./pi.ts";

export type BridgeMessage =
	| { type: "pi"; event: AgentEvent }
	| { type: "readout.batch"; entryId: EntryId; batch: ReadoutBatch }
	| { type: "readout.turn"; entryId: EntryId; turn: ReadoutTurnRecord }
	| SettingsMessage
	| ReadoutErrorMessage;

/** What a client starts from: Pi's snapshot at attachment, and the conversation's settings with their version. */
export type BridgeSnapshot = { pi: SnapshotEvent; settings: SettingsMessage };

export interface Bridge {
	readonly snapshot: BridgeSnapshot;
	/** Stop listening, wait until every message already queued is sent, and reject with the first failed send. */
	stop(): Promise<void>;
}

/** The bridge message for one of Pi's events: readout entries get their own types. */
function messageOf(event: AgentEvent): BridgeMessage {
	if (event.type === "entry_appended" && BatchEntry.is(event.entry)) {
		return { type: "readout.batch", entryId: event.entry.id, batch: event.entry.data };
	}
	if (event.type === "entry_appended" && TurnEntry.is(event.entry)) {
		return { type: "readout.turn", entryId: event.entry.id, turn: event.entry.data };
	}
	return { type: "pi", event };
}

export async function openBridge(
	harness: HarnessT,
	host: ReadoutHost,
	conversationId: ConversationId,
	context: Context,
	send: (message: BridgeMessage) => void | Promise<void>,
): Promise<Bridge> {
	let chain: Promise<void> = Promise.resolve();
	let failure: { error: unknown } | undefined;
	/** Queue one message behind every earlier one. The chain never rejects: a failed send is kept for stop(). */
	const deliver = (message: BridgeMessage): Promise<void> => {
		chain = chain.then(async () => {
			if (failure !== undefined) return; // delivery ended at the first failure
			try {
				await send(message);
			} catch (error) {
				failure = { error };
			}
		});
		return chain;
	};
	let seen = -1;
	let held: SettingsMessage[] | undefined = [];
	const forward = (m: SettingsMessage): void => {
		if (m.version <= seen) return; // a duplicate, or older than what the client has
		seen = m.version;
		void deliver(m);
	};
	const offSettings = host.onSettings((m) => {
		if (m.conversationId !== conversationId) return;
		if (held !== undefined) held.push(m);
		else forward(m);
	});
	const offErrors = host.onError((m) => {
		if (m.conversationId === conversationId) void deliver(m);
	});
	try {
		const settings = await host.settings(conversationId); // read after subscribing: no change falls in the gap
		seen = settings.version;
		const early = held;
		held = undefined;
		for (const m of early) forward(m);
		const events = await watchEvents(harness, conversationId, context);
		events.start(async (batch) => {
			for (const event of batch) await deliver(messageOf(event));
		});
		return {
			snapshot: { pi: events.snapshot, settings },
			stop: async () => {
				offSettings();
				offErrors();
				await events.stop();
				// A Pi batch already being delivered may still queue messages: wait until the chain stops growing.
				for (let pending = chain; ; pending = chain) {
					await pending;
					if (pending === chain) break;
				}
				if (failure !== undefined) throw failure.error;
			},
		};
	} catch (error) {
		offSettings();
		offErrors();
		throw error;
	}
}
