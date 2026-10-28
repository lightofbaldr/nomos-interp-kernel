# Pinned agent package

`glimmer-agent-a287b8c.tgz` is an npm package built by `npm pack` from private
`lightofbaldr/glimmer-agent`, commit `a287b8c257ad46f9d0350f25487020ecc9e2256d`.

Archive SHA-256: `21571442d1d9d7dcf991fb91bc2c8e51383d6c0d61873708efdb724c75990126`.
The npm lock also binds SHA-512 integrity and Pi dependencies at 1.3.0.

The five modules have unchanged semantics from reviewed `6ea3078`; the successor
adds ordinary compiled ESM exports and a kernel-launcher pin. The app imports the
package, not copied or forked provider/extension/bridge source. Update this artifact
only from a reviewed agent commit. No capture data or model assets are in the archive.
