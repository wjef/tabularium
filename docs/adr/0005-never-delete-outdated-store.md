# 0005 — Never delete; self-contained outdated store

Date: 2026-10-17 · Status: Accepted

## Context
Documents must never be deleted, but obsolete ones should leave the active archive, with their
metadata stored separately. Retention (default 5 years) must prevent premature outdating.

## Decision
- "Outdate" is manual and blocked while `retain_until` is in the future.
- File moves to the Outdated folder (same hash name); metadata moves to `outdated.db`.
- `outdated.db` stores a **snapshot** of the document's tag paths and entity details plus its
  history, so later changes in the active archive cannot break it.
- Outdated documents are accessed in a read-only **Outdated section of the same app**
  (2026-10-18; replaces the planned separate shortcut — one running app is simpler and
  gives the same guarantees): search, preview, download, restore.
- `outdated.db` schema v1 is created with `create_all`; later changes need migrations.
- Restore re-links to existing tags/entities where possible, recreates missing ones.

## Consequences
- One codebase for both modes; mistakes are reversible.
- Two databases must be kept consistent; operations follow a crash-safe order
  (copy → move file → verify → remove from active).
