# Pinned agent package

`glimmer-agent-181a25a.tgz` is an npm package built by `npm pack` from private
`lightofbaldr/glimmer-agent`, commit `181a25a6e1092738f6942e61854168e06c813ff8`.

Archive SHA-256: `6b2ee37a64bcc3479f77308c7a803f20d46671f76d4bf9b8548748b4a9b11231`.
The npm lock also binds SHA-512 integrity and Pi dependencies at 1.3.0.

The five modules have unchanged semantics from reviewed `6ea3078`; the successor
adds ordinary compiled ESM exports and a kernel-launcher pin. The app imports the
package, not copied or forked provider/extension/bridge source. Update this artifact
only from a reviewed agent commit. No capture data or model assets are in the archive.
