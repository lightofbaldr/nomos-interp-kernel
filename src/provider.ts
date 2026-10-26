/**
 * The `nomos` provider (spec section 4; rulings P3-R1, P3-R2, P3-R3 and P3-R9). It is pi-ai's stock openai-completions
 * stream against the Nomos serve, plus two seams for a request the extension registered:
 * - onPayload adds the request's `nomos` block to the body;
 * - onProviderStreamEvent, which the stock stream awaits for every parsed chunk before it normalizes the chunk, hands
 *   the chunk to the request's sink, so the reply waits for the sink's writes (the 2026-10-24 persistence lifecycle).
 * The stream's terminal message goes to the sink once. The Pi events, `responseId`, reasoning, tools, the error path and
 * the HTTP connection are the stock provider's.
 */
import type { AssistantMessage, AssistantMessageEventStream, Message, Model, ProviderStreams, StreamOptions } from "@earendil-works/pi-ai";
import { stream as completionsStream, streamSimple as completionsStreamSimple } from "@earendil-works/pi-ai/api/openai-completions";
import { createProvider, type Provider } from "@earendil-works/pi-ai/models";
import type { NomosBlock } from "./contract.ts";

export const NOMOS_PROVIDER = "nomos";
export const NOMOS_MODEL = "nomos-muse-glimmer-30b";

/** Where one request's readouts go. */
export interface ReadoutSink {
	/** One parsed SSE chunk, before Pi normalizes it. The reply waits for the promise, which never rejects. */
	chunk(data: unknown): Promise<void>;
	/** The stock stream's terminal message (stop, length, toolUse, error or aborted). Called once; never throws. */
	end(message: AssistantMessage): void;
}

export interface ReadoutRequest {
	readonly block: NomosBlock;
	readonly sink: ReadoutSink;
	/** The last message of the generation's context: only the call that sends it takes the request (ruling P3-R9). */
	readonly last: Message | undefined;
}

/** The extension registers a conversation's next model request here, by its provider session ID (ruling P3-R2). */
export class ReadoutRequests {
	readonly #bySession = new Map<string, ReadoutRequest>();

	register(sessionId: string, request: ReadoutRequest): void {
		this.#bySession.set(sessionId, request);
	}

	/**
	 * The request registered for this session, handed out once, and only to the call whose context ends with the
	 * request's last message. A compaction shares the session ID but sends other messages, so it goes without readouts.
	 */
	take(sessionId: string | undefined, messages: readonly Message[]): ReadoutRequest | undefined {
		if (sessionId === undefined) return undefined;
		const request = this.#bySession.get(sessionId);
		if (request === undefined || messages.at(-1) !== request.last) return undefined;
		this.#bySession.delete(sessionId);
		return request;
	}

	/** Remove the registration of `sink` if no call took it. True when it was still waiting. */
	withdraw(sessionId: string, sink: ReadoutSink): boolean {
		if (this.#bySession.get(sessionId)?.sink !== sink) return false;
		this.#bySession.delete(sessionId);
		return true;
	}
}

export interface NomosProviderOptions {
	/** The serve's OpenAI base, e.g. http://127.0.0.1:8091/v1. */
	readonly baseUrl: string;
	readonly requests: ReadoutRequests;
	readonly modelId?: string;
	readonly contextWindow?: number;
	readonly maxTokens?: number;
}

export function nomosModel(options: Omit<NomosProviderOptions, "requests">): Model<"openai-completions"> {
	return {
		id: options.modelId ?? NOMOS_MODEL,
		name: "Muse-Glimmer-30B (Nomos)",
		api: "openai-completions",
		provider: NOMOS_PROVIDER,
		baseUrl: options.baseUrl,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		reasoning: true,
		contextWindow: options.contextWindow ?? 32768,
		maxTokens: options.maxTokens ?? 4096,
		// The serve reads max_tokens and the system role, and has no store or reasoning-effort fields.
		compat: { maxTokensField: "max_tokens", supportsDeveloperRole: false, supportsStore: false, supportsReasoningEffort: false },
	};
}

/** The request's two seams, after any the caller set: its payload hook and its stream observer run first. */
function withReadout<O extends StreamOptions>(request: ReadoutRequest, options: O | undefined): O {
	const base = (options ?? {}) as O;
	const onPayload: NonNullable<StreamOptions["onPayload"]> = async (payload, model) => {
		const inner = await base.onPayload?.(payload, model);
		return { ...((inner ?? payload) as Record<string, unknown>), nomos: request.block };
	};
	const onProviderStreamEvent: NonNullable<StreamOptions["onProviderStreamEvent"]> = async (data, model) => {
		await base.onProviderStreamEvent?.(data, model);
		await request.sink.chunk(data);
	};
	return { ...base, onPayload, onProviderStreamEvent };
}

/** The stock stream; for a registered request, with its seams, and its terminal message handed to the sink. */
function readoutStream<O extends StreamOptions>(
	requests: ReadoutRequests,
	messages: readonly Message[],
	options: O | undefined,
	run: (options: O | undefined) => AssistantMessageEventStream,
): AssistantMessageEventStream {
	const request = requests.take(options?.sessionId, messages);
	if (request === undefined) return run(options);
	const events = run(withReadout(request, options));
	void events.result().then((message) => request.sink.end(message));
	return events;
}

export function nomosProvider(options: NomosProviderOptions): Provider {
	const api: ProviderStreams = {
		stream: (model, context, o) =>
			readoutStream(options.requests, context.messages, o, (seamed) =>
				completionsStream(model as Model<"openai-completions">, context, seamed),
			),
		streamSimple: (model, context, o) =>
			readoutStream(options.requests, context.messages, o, (seamed) =>
				completionsStreamSimple(model as Model<"openai-completions">, context, seamed),
			),
	};
	return createProvider({
		id: NOMOS_PROVIDER,
		name: "Nomos (local serve)",
		baseUrl: options.baseUrl,
		// Keyless: the serve takes no key, but every provider resolves one (pi-ai models.js refuses an unresolved provider).
		auth: { apiKey: { name: "Nomos (keyless local serve)", resolve: async () => ({ auth: { apiKey: "nomos-local" } }) } },
		models: [nomosModel(options)],
		api,
	});
}
