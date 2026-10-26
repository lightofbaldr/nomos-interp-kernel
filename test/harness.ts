/** Harness helpers: the nomos provider and the readout extension on a pi-durable Harness, against a fake serve. */
import assert from "node:assert/strict";
import { createModels } from "@earendil-works/pi-ai";
import { type ReadoutConfig, readoutExtension, ReadoutHost, type ReadoutTurnRecord, type Steer, TurnEntry } from "../src/extension.ts";
import {
	type Context,
	type Conversation,
	createRegistry,
	type EntryRecord,
	type Extension,
	Harness,
	MemoryStorage,
	type Storage,
	type StorageWrite,
} from "../src/pi.ts";
import { NOMOS_MODEL, NOMOS_PROVIDER, nomosProvider } from "../src/provider.ts";
import { ctx, fakeServe, type Handler, readoutHandler } from "./support.ts";

export const CONFIG: ReadoutConfig = { enabled: true, emotion: { layers: [20, 24, 29], directions: "d".repeat(64) }, workspace: null };
export const WORKSPACE = { layers: [36, 40, 44, 48], lens: "e".repeat(64), k: 8 };
export const STEER: Steer = { layer: 24, directions: "d".repeat(64), direction: "calm", strength: 0.7 };
export const AGENT = { agent: { model: { provider: NOMOS_PROVIDER, modelId: NOMOS_MODEL } } };

/**
 * MemoryStorage for the lifecycle tests, whose close() keeps the data, so a second Harness opened on it is a restart that
 * kept its storage. `delayMs` slows every commit. `hold` sees each commit's writes; when it returns a promise, that
 * commit is applied once the promise resolves.
 */
export function testStorage(
	options: { delayMs?: number; hold?: (writes: readonly StorageWrite[]) => Promise<void> | undefined } = {},
): Storage {
	const inner = new MemoryStorage();
	return new Proxy(inner, {
		get(target, name) {
			if (name === "close") return async (): Promise<void> => undefined;
			if (name === "commit" && (options.delayMs !== undefined || options.hold !== undefined)) {
				const { delayMs, hold } = options;
				return async (writes: readonly StorageWrite[], context: Context) => {
					if (delayMs !== undefined) await new Promise((resolve) => setTimeout(resolve, delayMs));
					await hold?.(writes);
					return target.commit(writes, context);
				};
			}
			const value: unknown = Reflect.get(target, name, target);
			return typeof value === "function" ? value.bind(target) : value;
		},
	});
}

/**
 * The readout host, its extension and the nomos provider on a Harness over `storage`. Retries wait 1 ms, not 2 s.
 * `after` extensions are installed after the readout extension, so their hooks run after its hooks.
 */
export async function openAgent(baseUrl: string, storage: Storage = new MemoryStorage(), after: Extension[] = []) {
	const host = new ReadoutHost(ctx);
	const models = createModels();
	models.setProvider(nomosProvider({ baseUrl, requests: host.requests }));
	const registry = createRegistry();
	registry.install(readoutExtension(host));
	for (const extension of after) registry.install(extension);
	const harness = await Harness.open(storage, { models, registry, settings: { retry: { baseDelayMs: 1 } } }, ctx);
	host.attach(harness);
	return { host, harness };
}

export async function setup(handler: Handler = readoutHandler(), storage?: Storage) {
	const serve = await fakeServe(handler);
	const { host, harness } = await openAgent(serve.url, storage);
	const root = await harness.root(ctx, AGENT);
	const ask = async (content: string): Promise<void> => {
		const settled = await (await root.submit({ type: "input", content }, ctx)).wait(ctx);
		assert.equal(settled.status, "done");
	};
	const close = async (): Promise<void> => {
		await host.close();
		await harness.close(ctx);
		await serve.close();
		assert.deepEqual(host.errors, [], "the host reported errors");
	};
	return { serve, host, harness, root, ask, close };
}

export async function entries(conversation: Conversation): Promise<EntryRecord[]> {
	const watch = await conversation.watch(ctx);
	const list = [...watch.value.entries];
	await watch.stop();
	return list;
}

/** The conversation's turn records, oldest first. */
export async function turnsOf(conversation: Conversation): Promise<ReadoutTurnRecord[]> {
	return (await entries(conversation)).flatMap((e) => (TurnEntry.is(e) ? [e.data] : []));
}
