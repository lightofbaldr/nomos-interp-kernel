import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

// SHA256 of package.json in the Pi 1.3.0 tarballs audited 2026-10-24.
const pins = {
  "pi-ai": "4e9ed07b06a91d844485874c251f312b610d015a4a4fd32c78cde58a7862a3f1",
  "pi-durable": "7877411c036932f74af4226563ff37f121013a9f69d4f9ceb55f5cdbcf6e5c54",
};
for (const [name, pin] of Object.entries(pins)) {
  const bytes = readFileSync(new URL(`../node_modules/@earendil-works/${name}/package.json`, import.meta.url));
  const sha = createHash("sha256").update(bytes).digest("hex");
  assert.equal(sha, pin, `${name}: package.json differs from the audited tarball`);
  assert.equal(JSON.parse(bytes).version, "1.3.0");
  console.log(`${name} 1.3.0 package.json ${sha}`);
}
const chord = JSON.parse(readFileSync(new URL("../node_modules/@earendil-works/chord/package.json", import.meta.url)));
assert.equal(chord.version, "1.3.0");
console.log("VENDOR PINS OK");
