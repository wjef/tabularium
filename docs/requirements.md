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
1. **Ignore** (silently): names matching built-in or `ignore` patterns (`~$*`, `*.tmp`,
   `*.crdownload`, …), dot-files, hidden/system files, and the `_processed`, `_duplicates`,
   `_failed` folders.
2. **Skip** (logged as `SKIPPED`): OneDrive cloud-only placeholders — set the ingest folder to
   "Always keep on this device".
3. **Wait** (not logged, retried next scan): files created or modified less than
   `stable_seconds` ago (future timestamps are ignored), files open in another program, and
   files whose size or modification time changed while being copied.
4. **Copy + hash** in one pass to `<archive>\.tmp\`, computing SHA-256.
5. **Duplicate?** If the hash already exists (active; outdated too from M7): discard the copy,
   move the original to `<ingest>\_duplicates\`, log it.
6. **Finalize:** atomic rename to the final archive name and re-verify the hash. An identical
   file already there (left by an interrupted run) is reused. The original modification time is
   kept in the database (`original_mtime`), not on the archive copy.
7. **Record** the document in the database (it now appears in the *To be tagged* view).
8. **Original:** moved to `<ingest>\_processed\` (same subfolder structure; name collisions get
   ` (1)`, ` (2)`… suffixes). If it cannot be moved, it stays and becomes a duplicate next scan.
9. **Any error in 4–7:** the archive copy is removed again, the original moved to
   `<ingest>\_failed\`, reason logged (`FAILED`). Nothing is lost silently.

The order (copy → verify → record → move original) guarantees a crash at any step loses nothing.
Subfolders of the ingest folder are scanned (`include_subfolders`); the subfolder path is stored
as `original_relpath` but has no other meaning. Only one scan runs at a time.

### 3.3 Archive naming
- `<archive>\<first 2 hex chars>\<full sha256>.<original extension, lowercase>`
  e.g. `3f\3fa9c1…e81b.pdf`.
- The original filename and relative ingest path are stored in the database.

## 4. Object model

```
EntityType  id, name, normalized_name (unique), sort_order — user-defined
EntityMembership  member_id → Entity, group_id → Entity (many-to-many, nestable, no loops)
Entity      id, type_id → EntityType, name, normalized_name,
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
- **Delete** is only possible for a tag without documents and without sub-tags.
- **Merge** A into B: documents tagged A get B (no duplicate links), sub-tags of A move under B,
  A is removed. Refused when B is A or below A, or when B already has a sub-tag with the same
  name as one of A's (nothing changes then). Every document re-tag is recorded in its history.

### 4.2 Entities
- A person/organization is defined once, with its details.
- A tag may point to an entity; its display name then comes from the entity.
- The same entity can appear under several facets (`sender / Jan Kowalski` and
  `recipient / Jan Kowalski` are two tags pointing to one entity).
- Entity types are user-defined (Person, Organization, Institution and Other exist at
  start and can be renamed or deleted; add e.g. Bank, School). A type can only be deleted
  when no entity uses it.
- Entities can belong to other entities (person → family → extended family; a person can
  be in several groups). Loops are refused. Managed on the entity page (Member of /
  Members); deleting an entity removes its memberships.
- An entity can only be deleted when no tag points to it. Details are free-form
  `key: value` pairs (tax id, address, e-mail…); aliases are a list.

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
- Manual action "Outdate" on the document page (optional reason), offered only after
  `retain_until` has passed.
- Crash-safe order: copy the file to the Outdated folder (same hash name) and verify →
  write `outdated.db` → remove the document from the active database (tag links and
  relations go with it; its audit history stays) → remove the archive file. A retry after a
  crash continues where it stopped.
- `outdated.db` is **self-contained**: each document keeps a snapshot of its tags (paths),
  the entities behind them (type, details, groups), its relations and its history. Later
  renames or deletions in the active archive do not affect it.
- The Outdated folder has its own `metadata.json`; the Maintenance restore rebuilds
  `outdated.db` from it too.
- **Outdated** section of the app (decided during M7 instead of a separate read-only
  instance — same result, one app): list and text search, preview, download, snapshot,
  history, **Restore to the archive** — no tagging or editing.
- Restore re-links the original tags where they still exist and recreates missing tags and
  entities from the snapshot; relations are restored when the other document is active.
- Ingesting a file whose hash is outdated counts as a duplicate.
- If `outdated.db` is empty while the Outdated folder's `metadata.json` lists documents
  (lost database, new PC), outdating and restoring are paused and the Maintenance page
  offers **Restore the outdated store**; that `metadata.json` is never overwritten by an
  empty store.
- History links outdated documents to the Outdated section.
- Known limitations: no integrity check of the Outdated folder yet; no backups of
  `outdated.db` (its `metadata.json` is the safety net); a crash after the database
  steps but before the archive file is removed leaves that file behind (the integrity
  check reports it as an orphan); when both documents of a relation are outdated one
  after the other, the second snapshot no longer contains the link.

## 8. User interface (v1: simple and brutal)
- Server-rendered HTML + htmx, served on `127.0.0.1` only, opened in the default browser.
- **To be tagged** view: all active documents with zero tags.
- **Archive** view: tag-tree filter → document list (left) → preview (right).
- Preview inline for PDF, PNG/JPEG/GIF/WebP/BMP images and plain text. Every other type
  (including HTML, SVG, Office files) is only offered as a download, never rendered in the
  app's origin.
- **Open copy in default app**: a read-only copy with a readable name is made in
  `<app home>\tmp\open` (cleaned after a day) and handed to Windows, so an editor can never
  change the archived file. **Show in Explorer** selects the archived (hash-named) file.
- **Export**: selected documents as a zip of copies named `YYYY-MM-DD <title or sha256>.<ext>`
  (date only if set; never the original name) (Windows-safe, de-duplicated) plus `index.csv` (name, original name, date,
  title, tags, SHA-256; UTF-8 with BOM for Excel).
- Tags are added by typing a full path (`sender / Jan Kowalski`, case/diacritic-insensitive)
  from a suggestion list, by picking *under which tag + which entity* (the entity tag is
  reused or created), or by creating a new tag on the spot.
- Document details: title, note, document date, *keep until* (automatic or manual).
- **Document name in the app** = its title, otherwise its archive file name
  (`<sha256>.<ext>`). Original file names are often random (scanner/phone names), so
  they are shown only as reference in the document's *File* section (still searchable).
- Tagging: add/remove tags, create tags inline, pick entities, **bulk tagging** of a selection.
- English UI; all strings translatable (gettext); language selectable in config/UI later.
  Known gap: error messages raised by domain services are English-only for now.

### 8.1 Search semantics
- Selecting a tag includes all its descendants. Ticking a tag *and* one below it narrows to
  the lower one (`sender` + `sender / Jan Kowalski` = sent by Jan).
- **OR within a facet, AND across facets.**
- "Anything involving entity X" = documents with any tag pointing to X; several chosen
  entities form one more group (any of them). Optional per search: **include related
  entities** — also every group the chosen entities belong to and every member they have
  (all levels; siblings are not included). The tag tree filter is always exact.
- Additional filters: text in title, original file name and note (every word must match,
  case/diacritic-insensitive), document date range (documents without a date are then
  excluded). Results: newest document date first, undated last; first 500 shown.
- Search is a GET form, so every search is a bookmarkable URL.
- Known gap (ADR 0009): text search uses a SQLite function (`tnorm`) for case/diacritic
  folding; a PostgreSQL backend needs an equivalent (e.g. `unaccent` + `lower`).
- "Open copy" refuses programs and scripts (`.exe`, `.bat`, `.ps1`, `.js`, `.lnk`, …):
  copies lose the internet-origin marker that normally makes Windows warn.

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
- Database backup (SQLite backup API) on every start and before every migration; manual
  backup on the Maintenance page. Only startup backups are pruned (newest `[backup] keep`,
  10); an empty database is not backed up at startup, so a lost database cannot push the
  last good backups out. Upgrade and manual backups are kept.
- `metadata.json` in the archive root: every table as readable JSON (plus tag paths and
  display names), rewritten atomically within `metadata_interval_seconds` (30) after any
  committed change, at start and on shutdown. Not included: the ingest log.
- Integrity check (Maintenance page, quick = presence + size, full = re-hash): missing
  files, changed files, orphan files in the archive (e.g. OneDrive conflict copies; an
  orphan with the content of a missing document is pointed out), cloud-only placeholders
  (reported, never downloaded). It only reports; nothing is changed or deleted.
- **Restore:** into an empty database of the same schema version, from the archive's
  `metadata.json` (Maintenance page); followed by an integrity check. Tested as a round
  trip (export → rebuild → identical tables).
- **Moving PCs** = copy the archive folder, point the new installation at it, restore.
- **Restore pending** (empty database, archive `metadata.json` with data — a new PC or a lost
  database): `metadata.json` is never written and nothing is ingested until the restore is
  done; the home page points to it.
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
