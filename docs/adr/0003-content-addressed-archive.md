# 0003 — Ingest folder → content-addressed archive

Date: 2026-10-17 · Status: Accepted

## Context
Files must be stored unchanged, matched reliably to their metadata, and named uniquely. The user
drops files into an ingest folder (possibly from a phone via OneDrive) and tags them later.

## Decision
- Ingest runs on start and on request. Files are copied and hashed (SHA-256) in one pass,
  verified, recorded, then the original is moved to `_processed\` (`_duplicates\`, `_failed\`
  for the other outcomes).
- Archive name: `<2 hex>\<64 hex sha256>.<ext>`. Original name kept in the database and in
  `metadata.json`.
- Files that are still changing size, locked, temporary or OneDrive placeholders are skipped.

## Consequences
- Duplicates are detected for free; any file can be verified by re-hashing.
- Names are unreadable in Explorer — browsing happens in the app, and export restores
  readable names.
- Sharding keeps directories small.
