# 0006 — Public repository hygiene

Date: 2026-10-17 · Status: Accepted

## Context
The repository is public on GitHub. The archive contains personal and financial documents.

## Decision
- **No real data in the repo:** real config, databases, documents and paths live only in
  app home (ADR 0002). The repo ships `tabularium.example.toml` with placeholder paths.
  Tests generate their own files and use fictional names.
- **Personal denylist:** a pre-commit and commit-msg hook (`scripts/check_denylist.py`) blocks
  commits whose file contents, file names or message contain any word from
  `%LOCALAPPDATA%\Tabularium\denylist.txt` (case- and diacritic-insensitive). The list itself
  never enters the repo. The check **fails closed**: a missing list blocks the commit;
  `TABULARIUM_DENYLIST=none` is the explicit opt-out (e.g. for an outside contributor).
- **Secrets:** gitleaks pre-commit hook; GitHub push protection enabled on the repo. Future keys
  go in environment variables or Windows Credential Manager (`keyring`), never config files.
- **Author e-mail:** GitHub noreply address (set globally), with GitHub's
  "Block command line pushes that expose my email".
- Licence: MIT.

## Consequences
- Defence in depth: rules + automated checks. Screenshots, issues and pasted stack traces
  still need manual care.
