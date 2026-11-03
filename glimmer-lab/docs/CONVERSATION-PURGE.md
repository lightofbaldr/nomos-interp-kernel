# Conversation data controls

Open **Manage conversation data** at the bottom of the sidebar (inside navigation on a phone). Review a selected conversation or choose **Purge all…**. Type `DELETE` or `PURGE ALL` into the confirmation before the destructive button becomes available. Cancel and closing the dialog do not delete anything.

Deletion removes the selected server-side Pi conversations, entries (chat, reasoning, readouts, annotations, profile snapshots and tool results), tasks, submissions, scoped documents and all document revisions. The conversation sandbox files go too. Purge all includes leftover numeric conversation workspaces even if their conversation is already absent. Installation-probe files and empty serialization lock files remain. IDs are never reset or reused.

Recorded examples, steering-diff datasets, model/tokenizer/readout assets, original imported harnesses, external logs/backups and provider-held data are outside this operation. The shared kernel KV cache is also outside its scope: the UI does not flush or restart a kernel. SQLite secure-delete and compaction reduce ordinary database remnants, but this is **not secure physical erasure**, a backup-erasure claim, or a promise to retract data already sent to a provider.

## Admission and persistence

- Same-origin loopback checks apply to preview and deletion; the API accepts only the explicit target, revision and confirmation fields. A browser never supplies a path or SQL.
- Every runtime HTTP operation, including a complete annotation call, holds a manager lease. Deletion claims an exclusive barrier synchronously and refuses if a lease or generation is active. It checks the durable scheduler plus all Live/Inbox documents and, again, persisted task/submission status. All conversations must be idle, even for selected deletion, because the harness is reopened afterward.
- A preview is bound to the scope, exact conversation IDs, Pi sequence metadata and record counts. New work or settings changes invalidate it. Confirmation text is checked on the server too.
- Pi 1.3.0 has no deletion API. The adapter deliberately closes the harness, its bridges, readout host, and sandbox broker before using SQLite. It refuses unknown tables, columns, views, triggers or schema versions. Upgrade this adapter with the Pi version and re-run the persisted/reopen controls. A fork depending on selected history blocks deletion of the ancestor; delete descendants first or purge all. Shared session documents also block selected deletion.
- The filesystem helper accepts only the operator-derived database workspace root and numeric IDs. It requires private, non-symlinked parent directories, holds all workspace locks before removing any file, refuses active/unknown orphan sandbox units, and walks directory descriptors without following symlinks. It does not stop an orphaned unit for the operator. Single-app ownership and no concurrent operator writes to the DB/workspaces remain operational assumptions.
- Filesystem deletion and SQLite are not one atomic transaction. File cleanup comes first, under the database write lock. A failed cleanup retains database records, but some files may already be gone; the error says so. A later SQL failure likewise reports that files may be gone while records remain. A crash can have the same partial outcome; inspect before retrying. No recovery backup of the deleted content is silently created.
- Database records delete in one transaction. Then VACUUM and WAL truncation reclaim space. Failure to reclaim space is a warning after successful logical deletion, not a fictitious rollback. The database sequence allocator remains monotonic.
- Maintenance closes old streams with an explicit host-only signal. Surviving conversations reattach; deleted conversations drop their browser state. Storage events refresh same-browser tabs too. The app does not create an empty replacement on reload after a purge; **New conversation** starts one explicitly.

## Verification

Use Node 24.22.1. All checks below use temporary databases, temporary sandbox files and a fake HTTP kernel. None delete user conversations or call a real model or paid service.

```
node scripts/purge-tests.mjs
npm run check
npm run build
node scripts/purge-browser.mjs
node scripts/purge-regression.mjs
```

The browser check covers typed confirmation, cancellation, a selected purge while preserving another conversation, same-browser and independent-browser updates, stale and busy refusal, same-origin protection, all-data purge, reload without recreation, a fresh conversation afterward, and phone/desktop dialog layouts. Test screenshots are untracked under `preview/checks/purge-*.png`.
