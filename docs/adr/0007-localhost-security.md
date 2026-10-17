# 0007 — Localhost security

Date: 2026-10-17 · Status: Accepted

## Context
A server on `127.0.0.1` is reachable by any web page open in the browser (CSRF) and, via DNS
rebinding, potentially readable by it.

## Decision
- Bind to loopback only; config validation rejects other hosts.
- **Host allow-list** (`127.0.0.1`, `localhost`) — defeats DNS rebinding.
- For state-changing methods: **Origin** (when present) must be our own origin, and an
  `X-CSRF-Token` header must match the per-process token. htmx sends the header globally via
  `hx-headers` on `<body>`.
- Files are served by document ID only, never by path.

## Consequences
- Plain HTML form posts without htmx are rejected; all mutations go through htmx or JS.
