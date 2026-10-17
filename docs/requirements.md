# Tabularium — Requirements

Tabularium is a local, single-user document archive. Files are stored unchanged; all
metadata (tags, entities, history) lives separately and is matched to files by hash.

Status: agreed specification, v1 scope. Decisions and their reasoning are in [`docs/adr/`](adr/).

---

## 1. Principles

1. **Originals are never modified.** File bytes stored in the archive are identical to the
   ingested file. Only the file *name* changes (see §3.3).
2. **Documents are never deleted.** They can be *outdated* (moved aside), never destroyed.
3. **Metadata lives outside the files** in a database, plus a human-readable JSON export
   stored next to the archive so the archive survives without the app.
4. **Nothing personal in the repository.** The repo is public. Real paths, names, documents
   and secrets live only on the local machine (§9).
5. **Local first, cloud ready.** v1 runs on one PC; the design allows moving files and
   database to the cloud later without a rewrite (§10).

## 2. Locations and configuration

| What | Where | Synced by OneDrive? |
|---|---|---|
| Source code | the repo folder (may live in OneDrive) + GitHub | yes — fine |
| App home: config, database, backups, logs, lock file | `%LOCALAPPDATA%\Tabularium\` | **no** |
| Python virtual environment | `%LOCALAPPDATA%\Tabularium\venv\` | **no** |
| Ingest folder | configurable (typically OneDrive) | yes — fine |
| Archive folder | configurable (typically OneDrive) | yes — fine, files are immutable |
| Outdated folder | configurable (typically OneDrive) | yes — fine |

- App home can be overridden with environment variable `TABULARIUM_HOME` (used by tests).
- Config file: `<app home>\tabularium.toml`. On first run it is created from the bundled
  example and the app exits asking the user to fill in the paths.
- The database must not live in a synced folder (risk of corruption).
- Archive and Outdated folders should be set to OneDrive "Always keep on this device".

## 3. Ingest

### 3.1 Trigger
- On application start (`scan_on_startup = true`) and on request ("Scan ingest folder" button).
- Live folder watching: later.

### 3.2 Pipeline (per file)
1. **Skip** files matching `ignore` patterns, OneDrive placeholders/temporary files, and files
   that are not *stable* (size changed within `stable_seconds`, or file is locked).
2. **Copy + hash** in one pass to `<archive>\.tmp\`, computing SHA-256.
3. **Duplicate?** If the hash already exists (active or outdated): discard the temp copy,
   move the original to `<ingest>\_duplicates\`, log it.
4. **Finalize:** atomic rename to the final archive name, re-verify the hash, preserve timestamps.
5. **Record** the document in the database (it now appears in the *To be tagged* view).
6. **Original:** moved to `<ingest>\_processed\` (configurable later to `delete`).
7. **Any error:** original moved to `<ingest>\_failed\`, reason logged. Nothing is lost silently.

The order (copy → verify → record → move original) guarantees a crash at any step loses nothing.
Subfolders of the ingest folder are scanned (`include_subfolders`), but carry no meaning.

### 3.3 Archive naming
- `<archive>\<first 2 hex chars>\<full sha256>.<original extension, lowercase>`
  e.g. `3f\3fa9c1…e81b.pdf`.
- The original filename and relative ingest path are stored in the database.

## 4. Object model

```
Entity      id, type (PERSON | ORGANIZATION | INSTITUTION | …), name, normalized_name,
            aliases[], attributes{} (tax id, address, e-mail, …)

Tag         id, parent_id (null = root), name, normalized_name, entity_id (nullable),
            sort_order, color          UNIQUE(parent_id, normalized_name)

Document    id (UUID), sha256 (UNIQUE), stored_path (relative to archive),
            original_filename, original_relpath, original_mtime, mime_type, size_bytes,
            title, note, document_date (plain date), retain_until (date),
            retain_until_manual (bool), ingested_at

DocumentTag     document_id, tag_id, tagged_at
DocumentRelation  from_document_id, to_document_id, type (e.g. PAYMENT_FOR, SIGNED_VERSION_OF)
                  — table in v1, UI later
AuditLog    id, at (UTC), actor (OS user name), action, object_type, object_id,
            old_value, new_value
IngestLog   id, run_id, source_path, sha256, result (IMPORTED | DUPLICATE | FAILED | SKIPPED),
            message, at
```

### 4.1 Tags and facets
- Tags form a tree. **Root tags act as facets** (e.g. `sender`, `recipient`, `category`).
- Tags can be nested to any depth (e.g. `category / taxes / PIT`).
- **All facets are multi-value** (a document may have two senders).
- **No tag is required.**
- Tags are referenced by ID: renaming, moving, merging never breaks assignments.
- A tag in use cannot be deleted silently: user must merge it or reassign its documents.

### 4.2 Entities
- A person/organization is defined once, with its details.
- A tag may point to an entity; its display name then comes from the entity.
- The same entity can appear under several facets (`sender / Jan Kowalski` and
  `recipient / Jan Kowalski` are two tags pointing to one entity).

### 4.3 Text normalization
- `normalized_name` = case-folded, diacritics removed, `ł → l`. Used for uniqueness and search,
  so `urzad` finds `Urząd`.

### 4.4 Dates
- All timestamps stored in UTC. `document_date` and `retain_until` are plain dates.

## 5. Retention
- Config: `[retention] years = 5`.
- Default `retain_until` = 31 December of (`document_date` year, or ingest year if no
  document date) + `years`.
- Recomputed when `document_date` changes, unless set manually (`retain_until_manual`).
- A document cannot be outdated before `retain_until`.
- Later: "eligible for outdating" filter and reminders.
- Note: the default is a simplification, not tax advice.

## 6. Change history
Every change to tags, entities, document fields, outdating and restoring is written to
`AuditLog` in the same transaction as the change itself.

## 7. Outdating (never delete)
- Manual action "Outdate", blocked while `retain_until` is in the future.
- Steps (crash-safe order): copy record + tag/entity **snapshot** + history to `outdated.db`
  → move file to the Outdated folder (same hash name) and verify → remove from active database
  → audit entry in both databases.
- `outdated.db` is **self-contained**: it stores tag paths and entity details as they were, so
  later renames in the active archive do not affect it.
- Access: the same application started in **read-only "Outdated" mode** (separate shortcut):
  search, preview, export, **restore to active**. No editing.

## 8. User interface (v1: simple and brutal)
- Server-rendered HTML + htmx, served on `127.0.0.1` only, opened in the default browser.
- **To be tagged** view: all active documents with zero tags.
- **Archive** view: tag-tree filter → document list (left) → preview (right).
- Preview inline for PDF, images, text. Other types: "Open in default app", "Show in Explorer".
- Tagging: add/remove tags, create tags inline, pick entities, **bulk tagging** of a selection.
- English UI; all strings translatable (gettext); language selectable in config/UI later.

### 8.1 Search semantics
- Selecting a tag includes all its descendants.
- **OR within a facet, AND across facets.**
- "Anything involving entity X" = documents with any tag pointing to X.
- Additional filters: filename/title text, document date range.

## 9. Security and privacy
- Server binds to loopback only. Host header allow-list (DNS-rebinding protection),
  Origin check and CSRF token on every state-changing request.
- Files are served by document ID, never by a path parameter.
- No secrets needed today. Any future key goes into environment variables or Windows
  Credential Manager (`keyring`) — never into config files or the repo.
- Repository hygiene: gitleaks pre-commit hook, GitHub push protection, git author e-mail is
  the GitHub noreply address, and a **personal denylist** check (pre-commit + commit-msg) whose
  word list lives outside the repo at `%LOCALAPPDATA%\Tabularium\denylist.txt`.
- Tests use generated documents and fictional names only.

## 10. Reliability, portability, future cloud
- Single running instance enforced by a lock file in app home.
- Database backup on each start (keep last N) + `metadata.json` export into the archive folder.
- Integrity check: missing files, hash mismatches, orphan files in the archive (e.g. OneDrive
  conflict copies), cloud-only placeholders (detected without downloading).
- **Restore test:** the database can be rebuilt from `metadata.json` + archive files.
- **Full export / import** to move the archive to another PC.
- Cloud readiness: SQLAlchemy 2 + Alembic (portable to PostgreSQL), file access through a
  storage interface (local now, blob storage later), UUIDs and relative paths only.

## 11. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Skeleton: repo, venv, FastAPI, SQLAlchemy/Alembic, config, lock file, security middleware, i18n setup, pre-commit (ruff, gitleaks, denylist), CI, docs | App starts with configured folders; tests and hooks pass |
| M1 | Domain model + migrations + normalization + retention rules + audit | Unit tests pass |
| M2 | Ingest pipeline | Files dropped into ingest appear in archive, hash-named, recorded |
| M3 | Tag tree and entity management, with history | Facet tree with entities can be built and edited |
| M4 | To be tagged view, tagging, bulk tagging, preview | Inbox can be emptied |
| M5 | Search/browse, export selection with readable names, open in app / show in Explorer | Facet searches return correct documents |
| M6 | Integrity check, backups, metadata.json, restore test, full export/import | DB can be deleted and rebuilt |
| M7 | Outdating, `outdated.db`, read-only Outdated mode, restore | Document round-trips active → outdated → active |
| Later | Retention reminders, relations UI, full-text search + OCR, live folder watch, cloud backend, real UI, packaging | — |
