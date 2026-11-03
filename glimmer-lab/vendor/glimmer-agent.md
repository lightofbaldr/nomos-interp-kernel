# Pinned agent package

`glimmer-agent-cb2d4ca.tgz` is the npm package built by `npm pack` from this repository's `glimmer-agent/` folder at
commit `cb2d4cac9b269ee03aeae116518176e1a055652d` (MIT, Light of Baldr LLC; see `../../glimmer-agent/LICENSE`).

Archive SHA-256: `d321dd66772887a1bd4d1ab5ddae927dc670d3ae67ac92bfc47d42c6dfc42adc`. The npm lock also binds its SHA-512
integrity and the Pi dependencies at 1.3.0. Its `dist/`, `package.json` and `README.md` were checked to be identical to a
fresh `tsc` build of that commit.

The app imports the package rather than copying the provider, extension or bridge source. Update this artifact only from
a reviewed agent commit. No capture data or model assets are in the archive. Earlier archives
(`a287b8c`, `478f17a`, `0b67613`, `a84c710`) were removed from the tree for the public release; they remain in history
and in the verification records that name them.
