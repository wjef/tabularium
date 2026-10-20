# Tabularium

A local document archive: drop files into an ingest folder, Tabularium stores them unchanged
(named by SHA-256 hash), and you tag them with nested tags and reusable entities (people,
companies, institutions). Search by tags, preview in the app.

> Status: **M7 — outdating**: all planned v1 milestones done. See [requirements](docs/requirements.md) and the
> [architecture decisions](docs/adr/README.md).

## Requirements
- Windows 10/11, Python 3.12+
- Node.js 24 LTS to build the interface (`winget install OpenJS.NodeJS.LTS`)

## Setup (development)

Run from a normal PowerShell window in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev.ps1
```

This creates the virtual environment in `%LOCALAPPDATA%\Tabularium\venv`, installs the project
and the git hooks, and builds the interface (`frontend/`, React + TypeScript).

Then:

1. Run `Tabularium.bat` once — it creates `%LOCALAPPDATA%\Tabularium\tabularium.toml`.
2. Edit that file: set your `ingest`, `archive` and `outdated` folders (they must exist).
3. Run `Tabularium.bat` again — the app opens in your browser.

## OCR for scans and photos (optional)

Text PDFs are searchable right away. To also search scans and photos, install Tesseract
with the Polish language pack:

```powershell
winget install --id UB-Mannheim.TesseractOCR
```

winget installs English only. Add Polish from a PowerShell started with **Run as
administrator**:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/pol.traineddata -OutFile "C:\Program Files\Tesseract-OCR\tessdata\pol.traineddata"
```

Then click **Maintenance → Check OCR again**; waiting documents are read automatically.

## Troubleshooting

**"An Application Control policy has blocked this file"** at start: Windows Smart App
Control refused one of SQLAlchemy's compiled speed-up files. Run `setup-dev.ps1` again; it
installs SQLAlchemy's pure-Python build, which behaves identically.

## Personal denylist (required for committing)

Create `%LOCALAPPDATA%\Tabularium\denylist.txt` with one word per line (your name, family
names, e-mail, user-profile folder name…). Commits containing any of them — in file contents,
file names or the commit message — are blocked. The list never leaves your machine.

Without the list, commits are blocked too. Contributors who don't need it can opt out with
`$env:TABULARIUM_DENYLIST = "none"`.

## Moving to a new PC

Copy (or sync) the **Archive** and **Outdated** folders — each contains its files plus a
`metadata.json`.
On the new PC: set up Tabularium, point `archive` in the config at that folder, start it,
open **Maintenance** and click **Restore from metadata.json** (offered while the database
is empty). Until then Tabularium does not ingest anything and does not touch
`metadata.json`, so nothing can block or overwrite the restore.

## Development

```powershell
$py = "$env:LOCALAPPDATA\Tabularium\venv\Scripts\python.exe"
& $py -m pytest
& $py -m ruff check .
```

## Licence
MIT — see [LICENSE](LICENSE).
