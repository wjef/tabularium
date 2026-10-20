# 0007 — Localhost security

Date: 2026-10-17 · Status: Accepted

## Context
A server on `127.0.0.1` is reachable by any web page open in the browser (CSRF) and, via DNS
rebinding, potentially readable by it.

## Decision
- Bind to loopback only; config validation rejects other hosts.
- **Host allow-list** (`127.0.0.1`, `localhost`) — defeats DNS rebinding.
- For state-changing methods: **Origin** (when present) must be our own origin, and an
  `X-CSRF-Token` header must match the per-process token. The front end reads the token from
  `GET /api/v1/session` (readable only by our own origin) and sends it on every change
  (ADR 0010).
- Files are served by document ID only, never by path.

## Consequences
- Plain HTML form posts are rejected; all changes go through the JSON API with the header.
