# Undici: reachability check, not an exploitation test

Observed on 2026-10-30 while implementing workbench C. No exploit, paid call,
live model request, dependency update, runtime install or service restart was made.

## Installed copies and paths

- `npm explain undici`: npm **6.29.0** comes from AI Elements → `ai` →
  `@ai-sdk/provider-utils`; OpenAI also declares an optional peer. The provider
  utils implementation can load it in `createSafeNodeFetch` for downloads.
  No app import/call of the AI SDK download path was found in `src/`. This is a
  static assessment of the current app, not a proof for every dependency path.
- The actual Node executable reports **v24.18.0**, bundled Undici **7.28.0** via
  `process.versions.undici`. This copy is separate from npm's lockfile.
- `src/lib/server/meta-client.ts` uses global `fetch` for the fixed Meta URL.
  `pi-service.ts` uses it for operator-configured health calls. The Nomos provider
  uses Pi's openai-completions client, which supplies a fetch implementation or
  falls back to OpenAI's global fetch. No patched fetch is injected by this app.
- No server-side EventSource/WebSocket construction, function-valued `tls`
  options, custom `checkServerIdentity`, or custom Undici Agent was found in the
  application source. Browser streaming is a separate implementation.

## Conclusion

**Do not classify the issue as unreachable.** The excessive-content-encoding DoS
advisory includes 7.28.0. A malicious upstream response can terminate the process
even when code catches the fetch rejection. The request paths exist here. Nomos
is operator-selected and Meta's URL is fixed; neither is a model-chosen arbitrary
URL. That narrows exposure, but is not a patch or a guarantee about upstreams.
[Upstream advisory](https://github.com/nodejs/undici/security/advisories/GHSA-xjj6-f33q-929c).

The separate dropped-`tls` callback advisory requires function-valued TLS options;
that trigger was not found in the reviewed app configuration. This does not clear
the independent DoS or the remainder of the audit graph.
[Upstream TLS advisory](https://github.com/nodejs/undici/security/advisories/GHSA-2m5v-pch6-74hx).

## Before the demo

Prime was notified in comms 1546. Arrange a patched Node runtime, verify its actual
bundled Undici version (the advisory's patched 7.x line starts at **7.30.1**), and
rerun the offline gates before coordinating a live E2E. Also update the transitive
npm copy to a patched release in a separately reviewed dependency change. An
npm-only bump **does not update Node's built-in fetch**. No audit suppression or
global runtime mutation was applied by this slice.
