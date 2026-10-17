# 0009 — Cloud readiness

Date: 2026-10-17 · Status: Accepted

## Context
v1 runs on one PC. Later, files and database may move to the cloud. Moving to another PC must be
possible via export/import.

## Decision
- Database access only through SQLAlchemy 2; schema changes only through Alembic migrations, so
  the same code can target PostgreSQL.
- File access only through a `Storage` interface (`put`, `open`, `exists`, `move`, …);
  `LocalStorage` now, blob storage later.
- UUID primary keys for documents; stored paths are relative to the storage root.
- Audit records carry an `actor` from day one.
- Full export (archive files + `metadata.json` + DB copy) and import for migrating machines.

## Consequences
- Slightly more abstraction up front (≈ half a day) in exchange for no rewrite later.
