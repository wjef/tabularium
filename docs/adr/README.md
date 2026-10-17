# Architecture Decision Records

Short records of significant decisions: context, decision, consequences.
New decisions get the next number; superseded ones are marked, never deleted.

| # | Decision | Status |
|---|---|---|
| [0001](0001-tech-stack.md) | Python + FastAPI + SQLite + htmx, SQLAlchemy + Alembic | Accepted |
| [0002](0002-data-locations.md) | App data in `%LOCALAPPDATA%`, not Program Files or OneDrive | Accepted |
| [0003](0003-content-addressed-archive.md) | Ingest folder → hash-named archive, originals untouched | Accepted |
| [0004](0004-tags-facets-entities.md) | Tag tree with root facets, tags may point to entities | Accepted |
| [0005](0005-never-delete-outdated-store.md) | Documents never deleted; self-contained outdated store | Accepted |
| [0006](0006-public-repo-hygiene.md) | Public repo: denylist, gitleaks, noreply e-mail, no real data | Accepted |
| [0007](0007-localhost-security.md) | Loopback only + Host/Origin checks + CSRF token | Accepted |
| [0008](0008-i18n.md) | English UI, gettext from day one | Accepted |
| [0009](0009-cloud-readiness.md) | Storage interface + portable DB layer for a future cloud move | Accepted |
