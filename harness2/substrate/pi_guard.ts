/**
 * harness2 runtime guard for Pi rollouts. Owned by the runtime, never by the editable
 * harness: the runner loads it with `-e` before any harness extension.
 *
 * OpenCode rollouts run behind OPENCODE_PERMISSION, which fences file access to the
 * workspace. Pi has no permission system, so this extension blocks the file tools when
 * their `path` resolves outside the workspace (HARNESS2_WORKSPACE_ROOT, default cwd) or
 * /tmp. `bash` is not guarded; see docs/PI.md for the residual risk and sandbox advice.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const FILE_TOOLS = new Set(["read", "write", "edit", "ls", "grep", "find"]);

function realpathSafe(p: string): string {
  try {
    return fs.realpathSync(p);
  } catch {
    return path.resolve(p);
  }
}

export default function (pi: ExtensionAPI) {
  const root = realpathSafe(process.env.HARNESS2_WORKSPACE_ROOT || process.cwd());
  const allowed = [root, realpathSafe("/tmp")];
  const inside = (p: string) => allowed.some((a) => p === a || p.startsWith(a + path.sep));

  pi.on("tool_call", async (event) => {
    if (!FILE_TOOLS.has(event.toolName)) return undefined;
    const raw = (event.input as { path?: unknown } | undefined)?.path;
    if (typeof raw !== "string" || raw.length === 0) return undefined;
    const abs = realpathSafe(path.resolve(root, raw));
    if (inside(abs)) return undefined;
    return {
      block: true,
      reason: `harness2 guard: ${raw} is outside the workspace ${root}; only files under the workspace (and /tmp) may be accessed`,
    };
  });
}
