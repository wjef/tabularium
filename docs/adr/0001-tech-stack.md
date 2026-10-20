# 0001 — Tech stack: Python + FastAPI + SQLite + htmx

Date: 2026-10-17 · Status: Accepted; the UI part is superseded by [0010](0010-single-page-front-end.md)

## Context
Single-user local archive. Needs inline preview of PDFs/images, a deliberately simple first UI
that will be redesigned later, and a path to the cloud. Python 3.12 and Node are installed;
Rust and .NET are not.

Options considered: local web app (Python), PySide6/Qt desktop, Electron + TypeScript, Tauri 2.

## Decision
- **Python 3.12, FastAPI, Uvicorn** — backend and HTTP API, served on `127.0.0.1`.
- **SQLite** via **SQLAlchemy 2** with **Alembic** migrations.
- **Jinja2 templates + htmx** for the first UI; no JavaScript build step.
- The browser renders PDF/images natively; other types are opened in the default app.
- pytest, ruff, pre-commit; venv + pip (uv may be adopted later).

## Consequences
- Fastest route to a working archive; preview comes free.
- UI is decoupled from the backend, so it can be replaced (SPA, pywebview window, Tauri shell).
- Runs in a browser tab, not a native window, until packaging work is done.
- Qt desktop was rejected because the UI would be tied to Qt; Electron for size; Tauri for the
  missing Rust toolchain and learning curve (remains a packaging option later).
