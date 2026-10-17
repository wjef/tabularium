# Tabularium — notes for Claude

Local single-user document archive. Spec: `docs/requirements.md`. Decisions: `docs/adr/`.

## Hard rules
- **Public repo.** Never commit real names, real paths (e.g. a user profile path), real documents,
  databases, config with real paths, or secrets. Use fictional names (Jan Kowalski, Anna Nowak,
  Example Sp. z o.o.) and generated test files only. The denylist hook enforces part of this.
- **Originals are never modified** — only copied, hashed, renamed by hash.
- **Documents are never deleted** — only outdated (ADR 0005).
- All schema changes go through Alembic migrations; all file access through the storage
  interface (ADR 0009) once it exists.
- All UI strings wrapped for gettext (ADR 0008).
- Timestamps in UTC; `document_date` / `retain_until` are plain dates.

## AppData redirection (Claude desktop app) — see ADR 0002
- Inside the Claude desktop app, writes to `%LOCALAPPDATA%` go to a private copy; reads fall
  through to the user's real folder.
- **Never run the app against the real app home / real config.** It would move the user's real
  OneDrive files while writing DB changes into the private copy. Always set `TABULARIUM_HOME` to a
  scratch folder with scratch ingest/archive/outdated folders.
- Never write to `%LOCALAPPDATA%\Tabularium` except Claude's own venv. In particular never create
  `denylist.txt` there; for commits set `TABULARIUM_DENYLIST` only if the user's list is not readable.

## Dev environment
- venv: `%LOCALAPPDATA%\Tabularium\venv` (create with `scripts\setup-dev.ps1`).
- Python: `$env:LOCALAPPDATA\Tabularium\venv\Scripts\python.exe`.
- Tests: `python -m pytest`. Lint: `python -m ruff check . ; python -m ruff format --check .`
- Tests set `TABULARIUM_HOME` to a temp dir — never touch the real app home in tests.
- Run the app: `Tabularium.bat` or `python -m tabularium`.
