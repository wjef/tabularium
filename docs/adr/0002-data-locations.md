# 0002 — App data in %LOCALAPPDATA%

Date: 2026-10-17 · Status: Accepted

## Context
The source code lives in a OneDrive-synced folder. A SQLite database in a synced folder risks
corruption (sync during writes, conflict copies). `Program Files` was considered, but it is for
read-only program binaries: writing there needs admin rights and may be silently redirected to
`VirtualStore`.

## Decision
- App home = `%LOCALAPPDATA%\Tabularium\` (resolved via `platformdirs`, overridable with
  `TABULARIUM_HOME`). It holds `tabularium.toml`, `data\` (databases), `backups\`, `logs\`,
  the instance lock file and the personal `denylist.txt`.
- The virtual environment lives at `%LOCALAPPDATA%\Tabularium\venv\` (thousands of small files
  would otherwise be synced constantly).
- Ingest, Archive and Outdated folders are configurable and may live in OneDrive: archived files
  are immutable, so syncing them is safe and acts as an off-site copy.
- A future packaged app installs per-user into `%LOCALAPPDATA%\Programs\Tabularium`.

## Consequences
- No admin rights needed; nothing user-specific inside the repo.
- The database itself is not synced — protected by backups and `metadata.json` in the archive.
