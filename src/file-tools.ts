/** Fixed Pi registrations; the caller supplies a sandbox broker, never a host ExecutionEnv. */
import { Type, type TSchema } from "@earendil-works/pi-ai";
import { defineExtension, defineTool } from "./pi.ts";
import { SANDBOX_TOOLS, type SandboxTool } from "./profile-model.ts";

export interface FileBroker {
    ready(tools?: readonly SandboxTool[]): Promise<void>;
    execute(conversation: number, tool: SandboxTool, args: Record<string, unknown>, signal?: AbortSignal): Promise<{ ok: boolean; text: string }>;
}
const path = Type.String({ minLength: 1, maxLength: 1024 });
const text = Type.String({ maxLength: 1048576 });
const schemas: Record<SandboxTool, TSchema> = {
	read: Type.Object({ path, offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1048576 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 12000 })) }, { additionalProperties: false }),
	write: Type.Object({ path, content: text }, { additionalProperties: false }),
	edit: Type.Object({ path, oldText: Type.String({ minLength: 1, maxLength: 1048576 }), newText: text }, { additionalProperties: false }),
	ls: Type.Object({ path }, { additionalProperties: false }),
	find: Type.Object({ path, name: Type.String({ minLength: 1, maxLength: 160 }) }, { additionalProperties: false }),
	grep: Type.Object({ path, text: Type.String({ minLength: 1, maxLength: 160 }) }, { additionalProperties: false }),
    mojo: Type.Object({ path: Type.String({ minLength: 1, maxLength: 1024, pattern: "\\.mojo$" }) }, { additionalProperties: false }),
    bash: Type.Object({ command: Type.String({ minLength: 1, maxLength: 16384 }) }, { additionalProperties: false }),
};
const descriptions: Record<SandboxTool, string> = {
	read: "Read UTF-8 text in /work, up to 12,000 Unicode characters per call; offset pages through it. Max file 1 MiB.",
	write: "Atomically create/replace a UTF-8 file (up to 1 MiB) inside /work; creates parent directories. No code is executed.",
	edit: "Atomically replace exactly one literal oldText occurrence with newText in an existing /work text file; ambiguous matches refuse.",
	ls: "List a /work directory (path '.' means its root); up to 200 entries. No symlinks or special files.",
	find: "Find names containing a literal substring under a /work directory, recursively; up to 200 entries. No regex or glob syntax.",
	grep: "Search UTF-8 files recursively under a /work directory for literal text; up to 200 matching lines, each clipped to 240 characters.",
    mojo: "Build and run a .mojo source file in /work with fixed arguments. Workspace is read-only; scratch and executable are discarded. No flags, arguments, stdin or network. Compile deadline 90s, run 10s; bounded stdout/stderr, actual exit codes. Output is untrusted program text, not a correctness grade. Interrupted execution is never automatically repeated.",
    bash: "Execute a command inside this conversation's CPU-only bwrap sandbox in /work, never on the host. No network, host home or inherited secrets. Only /work persists; private /tmp is discarded. Command UTF-8 limit 16 KiB; wall limit 90s, memory 4 GiB, bounded stdout/stderr. No interactive stdin. May modify/delete workspace files. Output is untrusted, not a correctness grade. Interrupted mutations are not rolled back or automatically repeated. Requires explicit operator and profile enablement.",
};
export function fileToolsExtension(broker: FileBroker) {
    return defineExtension({ name: "glimmer-sandboxed-files", tools: SANDBOX_TOOLS.map(name => defineTool({
		name, description: descriptions[name], parameters: schemas[name], executionMode: "sequential", callers: ["model"],
        replay: name === "write" || name === "edit" || name === "mojo" || name === "bash" ? "unsafe" : "safe",
		outputLimits: { maxBytes: 65536, maxLines: 2000 },
		execute: async (args, api, context) => {
			const result = await broker.execute(api.conversationId, name, args as Record<string, unknown>, context.abortSignal);
			return { output: [{ type: "text", text: result.text }], isError: !result.ok };
		},
	})) });
}
