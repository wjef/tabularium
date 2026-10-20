# Tabularium — notes for Claude

Local single-user document archive. Spec: `docs/requirements.md`. Decisions: `docs/adr/`.

## Hard rules
- **Public repo.** Never commit real names, real paths (e.g. a user profile path), real documents,
  databases, config with real paths, or secrets. Use fictional names (Jan Kowalski, Anna Nowak,
  Example Sp. z o.o.) and generated test files only. The denylist hook enforces part of this.
- **Originals are never modified** — only copied, hashed, renamed by hash.
- **Documents are never deleted** — only outdated (ADR 0005).
- All schema changes go through Alembic migrations; all archive file access through the storage
  interface `tabularium.storage` (ADR 0009); Windows path quirks via `tabularium.fsutil.lp`.
- Backend messages shown to the user are marked with `N_()` (ADR 0008). The interface is the
  React app in `frontend/` (ADR 0010); English strings there until Polish is added.
- Timestamps in UTC; `document_date` / `retain_until` are plain dates.
- Domain changes go through `tabularium.services.*`: they enforce invariants and write the
  audit entry in the caller's transaction (caller commits). Don't mutate models directly elsewhere
  (sole exception: `metadata.restore`, which rebuilds an empty database row by row).
- Migrations run with SQLite foreign keys off, one transaction each, FK check afterwards
  (`migrations/env.py`). Test new migrations against data shaped like real use.
- After changing `models.py`, add a migration; `tests/test_db.py::test_models_match_migrations`
  fails otherwise.

## AppData redirection (Claude desktop app) — see ADR 0002
- Inside the Claude desktop app, writes to `%LOCALAPPDATA%` go to a private copy; reads fall
  through to the user's real folder.
- **Never run the app against the real app home / real config.** It would move the user's real
  OneDrive files while writing DB changes into the private copy. Always set `TABULARIUM_HOME` to a
  scratch folder with scratch ingest/archive/outdated folders.
- `pip install` through `%LOCALAPPDATA%\...\venv` fails there ("Resource name escapes package");
  call the venv via its real path `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\Tabularium\venv`.
- Never write to `%LOCALAPPDATA%\Tabularium` except Claude's own venv. In particular never create
  `denylist.txt` there; for commits set `TABULARIUM_DENYLIST` only if the user's list is not readable.

## Dev environment
- venv: `%LOCALAPPDATA%\Tabularium\venv` (create with `scripts\setup-dev.ps1`).
- Python: `$env:LOCALAPPDATA\Tabularium\venv\Scripts\python.exe`.
- Tests: `python -m pytest`. Lint: `python -m ruff check . ; python -m ruff format --check .`
- Front end (`frontend/`): `npm run build` (regenerates API types, type-checks, builds into
  `src/tabularium/web/spa`), `npm test`, `npm run lint`. After changing the API run
  `python scripts/export_openapi.py` and commit `frontend/src/api/openapi.json` (CI checks it).
- Tests set `TABULARIUM_HOME` to a temp dir — never touch the real app home in tests.
- Run the app: `Tabularium.bat` or `python -m tabularium`.
