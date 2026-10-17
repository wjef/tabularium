# 0004 — Tag tree with facets and entities

Date: 2026-10-17 · Status: Accepted

## Context
Tags must be nestable (`sender → Jan Kowalski, Anna Nowak, Urząd Skarbowy`), editable at any
time, and able to refer to a person or organization whose details are stored once.

## Decision
- One `Tag` table forming a tree (`parent_id`). Root tags act as **facets**.
- All facets are multi-value; no facet is required. Untagged documents form the
  "To be tagged" view (derived, not a stored status).
- `Tag.entity_id` optionally points to an `Entity`. The same entity can appear under several
  facets via separate tags.
- Assignments reference tag IDs, so rename/move/merge never break them.
- Search: a selected tag includes descendants; OR within a facet, AND across facets.
- `normalized_name` (casefold, no diacritics, `ł → l`) for uniqueness and search.
- Entity types are rows in `entity_type`, managed by the user (2026-10-17; replaced the
  initial fixed list). Migration 0003 turned the four fixed values into editable rows.

## Consequences
- Both "sent by X" and "anything involving X" are answerable.
- Descendant queries use recursive CTEs — fine at personal-archive scale.
