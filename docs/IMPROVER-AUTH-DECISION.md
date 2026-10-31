# Improver authentication decision: B only

2026-10-31. Prime's ruling after the fake-credential `/proc` witness: remove A
(subscription-token pass-through), implement only B (host-side API-key
injection), and keep the default explicitly `off` / trusted-host. A is not an
option pending a warning or toggle. No existing Linux login is read or relayed.

## What the witness measured

The installed native Claude client called its Bash tool inside the proposed
private process namespace, against a fake API with a fake credential. Bash did
not inherit the credential variable directly. It could nevertheless read the
client's `/proc/<pid>/environ`, recover the fake token, and write it into retained
`/work` scratch. The recorded booleans were:

| Check | Result |
| --- | --- |
| Bash inherited environment credential | false |
| Bash read credential through `/proc` | true |
| Credential written into retained work | true |
| Native client fake API passed | true |
| Real credential read | false |
| Real provider contacted | false |

Private host containment did not isolate a client-held secret from that client's
own model-operated tools. Scrubbing captured stdout/stderr cannot prevent the
tool from copying a token into another file. That contradicts the intended
improver boundary, so the subscription implementation and its positive tests
were deleted instead of presenting that route as safe behind a caveat.

## Preserved evidence

The original local evidence remains unchanged:

- Directory: `/home/adam/Projects/harness2/.unlazy/improver-auth-feasibility/`
- `witness.py`: SHA-256
  `27f1a7869939b37346b785cad5df4c1e55c2a1140fce8e4c50d23829c220939d`
- `witness.log`: SHA-256
  `dfa35cc480e8a310bd6c219c6f17ad154283b3bf754f2b123bfe3389ecb5d778`

The same exposure was also reproduced by the native-client test
`test_fake_subscription_token_remains_tool_readable_through_proc` in
`tests/test_improver_namespace.py` at historical commit
`3debde3586afa891218fcc72c037397d4662f3d5`. Its passing result meant the exposure
was reproduced, **not** that credential isolation passed. That A-path test is
removed from the current tree along with the route. The archived witness belongs
to its earlier implementation; it is evidence, not a supported current launch
command. No real credential or paid request was used for either check.

## Remaining choices and admission

- `off` (default): original trusted, unsandboxed host improver. This describes
  its trust requirement; it is never an automatic fallback from B.
- `api-key` (B): the broker holds Adam's supplied `ANTHROPIC_API_KEY` and injects
  it only into its fixed upstream request. The sandbox receives an inert
  placeholder, uses `--bare`, and has no route to the host's process environment.
  Client Authorization headers are refused, not passed through.

B requires the existing `HARNESS2_MOJO_LIVE_APPROVED=1`, a separate
`HARNESS2_MOJO_IMPROVER_API_APPROVED=1`, and a valid supplied key. The separate
opt-in is Adam's per-token billing decision: an executor approval already set
for run 2 cannot enable API spending. Missing approval refuses before credential
loading or sandbox launch; missing/malformed credentials also refuse before
launch. This revision sets no flags, obtains no key, and makes no paid call.

The current evidence is offline/fake only. The revised real namespace/native
controls and Prime's six pending compiler/Pi controls run after the heldout
phase, before any new live authorization. No claim here extends the namespace
boundary to a hostile host or a compromised upstream provider.
