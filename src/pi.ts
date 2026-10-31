/**
 * The one module that imports pi-durable (spec section 4). Its README says "The API changes without notice between
 * releases", so every use sits behind this file, pinned to 1.3.0.
 */
import { Harness } from "@earendil-works/pi-durable";

export {
	AgentDoc,
	configure,
	createRegistry,
	defineDoc,
	defineEntry,
	defineExtension,
	GenerationTask,
	Harness,
	hook,
	MemoryStorage,
	LiveDoc,
	InboxDoc,
	ProviderDoc,
	section,
	watchEvents,
} from "@earendil-works/pi-durable";
export type {
	AgentEvent,
	Conversation,
	ConversationId,
	Cursor,
	EntryId,
	EntryRecord,
	Extension,
	HookApi,
	SnapshotEvent,
	Storage,
	StorageWrite,
	Tx,
} from "@earendil-works/pi-durable";
export { openNodeSqliteStorage } from "@earendil-works/pi-durable/storage/sqlite/node";
export { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
export type { Context } from "@earendil-works/chord";

/** An opened Harness. */
export type HarnessT = Awaited<ReturnType<typeof Harness.open>>;
