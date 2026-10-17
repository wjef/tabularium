# Tabularium

A local document archive: drop files into an ingest folder, Tabularium stores them unchanged
(named by SHA-256 hash), and you tag them with nested tags and reusable entities (people,
companies, institutions). Search by tags, preview in the app.

> Status: **M4 — tagging and preview** (search not yet). See [requirements](docs/requirements.md) and the
> [architecture decisions](docs/adr/README.md).

## Requirements
- Windows 10/11, Python 3.12+

## Setup (development)

Run from a normal PowerShell window in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1
```

This creates the virtual environment in `%LOCALAPPDATA%\Tabularium\venv`, installs the project
and the git hooks.

Then:

1. Run `Tabularium.bat` once — it creates `%LOCALAPPDATA%\Tabularium\tabularium.toml`.
2. Edit that file: set your `ingest`, `archive` and `outdated` folders (they must exist).
3. Run `Tabularium.bat` again — the app opens in your browser.

## Personal denylist (required for committing)

Create `%LOCALAPPDATA%\Tabularium\denylist.txt` with one word per line (your name, family
names, e-mail, user-profile folder name…). Commits containing any of them — in file contents,
file names or the commit message — are blocked. The list never leaves your machine.

Without the list, commits are blocked too. Contributors who don't need it can opt out with
`$env:TABULARIUM_DENYLIST = "none"`.

## Development

```powershell
$py = "$env:LOCALAPPDATA\Tabularium\venv\Scripts\python.exe"
& $py -m pytest
& $py -m ruff check .
```

## Licence
MIT — see [LICENSE](LICENSE).
