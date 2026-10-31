/** Browser-safe profile data. Only a fixed catalog; never discovery or code loading. */
export const PROFILE_POLICY = "glimmer.profile/files-1";
export const FILE_TOOLS = ["read", "write", "edit", "ls", "find", "grep"] as const;
export type FileTool = typeof FILE_TOOLS[number];
export const PROFILE_CONTEXT = 32768;
export const PROFILE_DEFAULT_CHARS = 6000;
export const PROFILE_MAX_BYTES = 65536;
export type Skill = { markdown: string; enabled: boolean };
export type Advisory = { name: string; text: string; enabled: boolean };
export type ProfileContent = { instructions: string; skills: Skill[]; advisory: Advisory[]; allowLarge: boolean; tools?: FileTool[] };
export const profileTools = (p: ProfileContent): FileTool[] => p.tools ?? [];
export type ProfileOrigin = {
	source: string; digest: string; files: { path: string; sha256: string; disposition: string }[];
	delivery: string; reference: string; referenceDigest: string; edited: boolean;
};
export type ProfileRevision = {
	revision: number; digest: string; policy: string; content: ProfileContent; origin: ProfileOrigin | null;
};
export type ProfileState = { owner: number; version: number; active: ProfileRevision | null; pending: ProfileRevision | null };
export type ProfileUsed = { task: number; attempt: number; cutoff: number; request: string | null; profile: ProfileRevision };
export const emptyContent = (instructions = ""): ProfileContent => ({ instructions, skills: [], advisory: [], allowLarge: false, tools: [] });
const bytes = (s: string) => new TextEncoder().encode(s).length;
function object(value: unknown, keys: string[]): asserts value is Record<string, unknown> {
	if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(k => !keys.includes(k))) throw Error("Unknown profile field or invalid object.");
}
function text(value: unknown, limit: number): asserts value is string {
	if (typeof value !== "string" || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value) || value.includes("\0") || bytes(value) > limit) throw Error("Profile text is invalid or too large.");
}

/** Deliberately small frontmatter subset: string name/description; folded/literal descriptions supported.
 * No YAML tags, aliases, collections or arbitrary metadata. Unsupported syntax is refused, not guessed. */
export function parseSkill(markdown: string) {
	text(markdown, 16384);
	const lines = markdown.replaceAll("\r\n", "\n").split("\n");
	if (lines[0] !== "---") throw Error("SKILL.md needs name and description frontmatter.");
	const end = lines.indexOf("---", 1);
	if (end < 0 || end > 80) throw Error("Missing or oversized skill frontmatter.");
	const fields: Record<string, string> = {};
	for (let i = 1; i < end; i++) {
		if (!lines[i].trim()) continue;
		const m = /^(name|description):\s*(.*)$/.exec(lines[i]);
		if (!m || m[1] in fields) throw Error("Use unique name/description string fields only.");
		let value = m[2];
		if ([">", ">-", "|", "|-"].includes(value)) {
			const block: string[] = []; while (i + 1 < end && /^\s/.test(lines[i + 1])) block.push(lines[++i].trim());
			value = block.join(value.startsWith(">") ? " " : "\n");
		} else if (value.startsWith('"')) {
			try { value = JSON.parse(value); } catch { throw Error("Invalid quoted skill string."); }
		} else if (value.startsWith("'")) {
			if (!value.endsWith("'") || value.length < 2) throw Error("Invalid quoted skill string.");
			value = value.slice(1, -1).replaceAll("''", "'");
		} else if (/^[!&*\[\]{}]|\s#|:\s/.test(value)) throw Error("Unsupported skill frontmatter syntax; quote the string.");
		text(value, 2048); fields[m[1]] = value;
	}
	if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(fields.name ?? "") || !fields.description?.trim()) throw Error("Skill needs a short unique name and a nonempty description.");
	const body = lines.slice(end + 1).join("\n").trim();
	if (!body) throw Error("Skill body is empty.");
	return { name: fields.name, description: fields.description, body };
}

export function profileSections(p: ProfileContent): string {
	return [
		...(profileTools(p).length ? ["## Sandboxed file workspace\nFile tools operate only in this conversation's /work. No shell, compiler or network. Restoring a profile does not undo files; forks start with an empty workspace. Skills below remain inline, not files."] : []),
		...p.skills.filter(s => s.enabled).map(s => { const k = parseSkill(s.markdown); return `## Inline skill: ${k.name}\n${k.description}\n\n${k.body}`; }),
		...p.advisory.filter(s => s.enabled).map(s => `## Advisory text (not enforced): ${s.name}\n${s.text}`),
	].join("\n\n");
}
export function renderedProfile(p: ProfileContent): string {
	const sections = profileSections(p);
	return [sections, p.instructions ? `<instructions>\n${p.instructions}\n</instructions>` : ""].filter(Boolean).join("\n\n");
}
export function profileSize(p: ProfileContent) {
	const rendered = renderedProfile(p);
	return { characters: rendered.length, bytes: bytes(rendered), approximateTokens: Math.ceil(rendered.length / 4), contextTokens: PROFILE_CONTEXT };
}
export function validateContent(value: unknown): ProfileContent {
	object(value, ["instructions", "skills", "advisory", "allowLarge", "tools"]);
	if (value.tools !== undefined && (!Array.isArray(value.tools) || value.tools.length > FILE_TOOLS.length || new Set(value.tools).size !== value.tools.length || value.tools.some(t => !FILE_TOOLS.includes(t as FileTool)))) throw Error("Unknown or duplicate file tool.");
	text(value.instructions, PROFILE_MAX_BYTES);
	if (typeof value.allowLarge !== "boolean" || !Array.isArray(value.skills) || value.skills.length > 16 || !Array.isArray(value.advisory) || value.advisory.length > 16) throw Error("Profile needs explicit flags and at most 16 skills/16 advisory sections.");
	const names = new Set<string>();
	for (const s of value.skills) {
		object(s, ["markdown", "enabled"]); text(s.markdown, 16384);
		if (typeof s.enabled !== "boolean") throw Error("Skill enabled must be boolean.");
		const k = parseSkill(s.markdown); if (names.has(k.name)) throw Error("Duplicate skill name."); names.add(k.name);
	}
	const advisoryNames = new Set<string>();
	for (const s of value.advisory) {
		object(s, ["name", "text", "enabled"]); text(s.name, 160); text(s.text, 16384);
		if (!s.name.trim() || advisoryNames.has(s.name) || typeof s.enabled !== "boolean") throw Error("Advisory sections need unique names and boolean flags.");
		advisoryNames.add(s.name);
	}
	const p = value as ProfileContent, size = profileSize(p);
	if (size.bytes > PROFILE_MAX_BYTES || bytes(JSON.stringify(p)) > 131072) throw Error("Profile exceeds the hard text/storage limit.");
	if (size.characters > PROFILE_DEFAULT_CHARS && !p.allowLarge) throw Error("Profile exceeds 6,000 rendered characters. Explicitly allow a larger profile first.");
	// Construct plain data, including when the caller is a reactive UI proxy. Stable field order also
	// prevents equivalent client key ordering from changing the content digest.
	return { instructions: p.instructions, skills: p.skills.map(s => ({ markdown: s.markdown, enabled: s.enabled })),
		advisory: p.advisory.map(s => ({ name: s.name, text: s.text, enabled: s.enabled })), allowLarge: p.allowLarge,
		tools: FILE_TOOLS.filter(t => (p.tools ?? []).includes(t)) };
}
