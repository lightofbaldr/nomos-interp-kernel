/** Fixed Pi registrations; the caller supplies a sandbox broker, never a host ExecutionEnv. */
import { Type, type TSchema } from "@earendil-works/pi-ai";
import { defineExtension, defineTool } from "./pi.ts";
import { FILE_TOOLS, type FileTool } from "./profile-model.ts";

export interface FileBroker {
	ready(): Promise<void>;
	execute(conversation: number, tool: FileTool, args: Record<string, unknown>, signal?: AbortSignal): Promise<{ ok: boolean; text: string }>;
}
const path = Type.String({ minLength: 1, maxLength: 1024 });
const text = Type.String({ maxLength: 1048576 });
const schemas: Record<FileTool, TSchema> = {
	read: Type.Object({ path, offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1048576 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 12000 })) }, { additionalProperties: false }),
	write: Type.Object({ path, content: text }, { additionalProperties: false }),
	edit: Type.Object({ path, oldText: Type.String({ minLength: 1, maxLength: 1048576 }), newText: text }, { additionalProperties: false }),
	ls: Type.Object({ path }, { additionalProperties: false }),
	find: Type.Object({ path, name: Type.String({ minLength: 1, maxLength: 160 }) }, { additionalProperties: false }),
	grep: Type.Object({ path, text: Type.String({ minLength: 1, maxLength: 160 }) }, { additionalProperties: false }),
};
const descriptions: Record<FileTool, string> = {
	read: "Read UTF-8 text in /work, up to 12,000 Unicode characters per call; offset pages through it. Max file 1 MiB.",
	write: "Atomically create/replace a UTF-8 file (up to 1 MiB) inside /work; creates parent directories. No code is executed.",
	edit: "Atomically replace exactly one literal oldText occurrence with newText in an existing /work text file; ambiguous matches refuse.",
	ls: "List a /work directory (path '.' means its root); up to 200 entries. No symlinks or special files.",
	find: "Find names containing a literal substring under a /work directory, recursively; up to 200 entries. No regex or glob syntax.",
	grep: "Search UTF-8 files recursively under a /work directory for literal text; up to 200 matching lines, each clipped to 240 characters.",
};
export function fileToolsExtension(broker: FileBroker) {
	return defineExtension({ name: "glimmer-sandboxed-files", tools: FILE_TOOLS.map(name => defineTool({
		name, description: descriptions[name], parameters: schemas[name], executionMode: "sequential", callers: ["model"],
		replay: name === "write" || name === "edit" ? "unsafe" : "safe",
		outputLimits: { maxBytes: 65536, maxLines: 2000 },
		execute: async (args, api, context) => {
			const result = await broker.execute(api.conversationId, name, args as Record<string, unknown>, context.abortSignal);
			return { output: [{ type: "text", text: result.text }], isError: !result.ok };
		},
	})) });
}
