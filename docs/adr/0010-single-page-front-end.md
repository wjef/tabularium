# 0010 — Single-page front end (React + TypeScript)

Date: 2026-10-20 · Status: Accepted · Supersedes the UI part of [0001](0001-tech-stack.md)

## Context
The first interface (server-rendered Jinja2 + htmx, ADR 0001) did its job but felt like a form
editor. The goal now is a calm, fast, keyboard-friendly workspace in the spirit of modern note
apps: sidebar with the tag tree, a document list with thumbnails, the open document with an
editable details panel, instant search, undo, a command palette. Partial page swaps could not
deliver that without growing into an ad-hoc client framework.

## Decision
- The interface is a **single-page app: React 19 + TypeScript**, built with **Vite**
  (`frontend/`). It is the only interface; the htmx pages were removed once the SPA covered
  everything they did.
- The backend is a **JSON API under `/api/v1`** (FastAPI, typed with pydantic). The frontend's
  TypeScript types are generated from the API's OpenAPI description
  (`scripts/export_openapi.py` → `frontend/src/api/openapi.json` → `schema.d.ts`); CI fails when
  the committed description is out of date.
- The build output goes to `src/tabularium/web/spa` (not committed) and is served by the same
  FastAPI process at `/`; every non-API path returns the app, which routes on the client.
  `scripts/setup-dev.ps1` builds it when Node.js is present.
- Data fetching and caching: TanStack Query. Routing: React Router. Icons: lucide. Font: Inter
  (OFL, bundled — no requests to third parties). Styling: CSS modules with design tokens;
  light and dark themes follow Windows unless chosen in the app.
- Own visual identity (teal accent); no other product's branding.
- Security is unchanged (ADR 0007): the app reads the CSRF token from `GET /api/v1/session`
  (same-origin only; other sites cannot read the answer, and the Host check stops DNS
  rebinding) and sends it as `X-CSRF-Token` on every change.

## Consequences
- Node.js is needed to *build* the interface, not to run the app. A packaged release will ship
  the built files.
- UI strings are plain English in the frontend for now; see the amendment in
  [0008](0008-i18n.md). Messages from the backend (domain errors) stay gettext-marked.
- Two toolchains to keep green: pytest/ruff and tsc/oxlint/vitest; both run in CI.
- The JSON API is a clean seam for a later desktop shell (pywebview/Tauri) or a cloud move.
