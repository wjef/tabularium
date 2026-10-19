"""Write the API description (OpenAPI) to frontend/src/api/openapi.json.

The front end generates its TypeScript types from it (`npm run gen:api`), so API and UI
cannot drift apart silently. Builds the app with throw-away folders; touches no real data.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend" / "src" / "api" / "openapi.json"


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        os.environ["TABULARIUM_HOME"] = str(tmp_path / "home")
        folders = {name: tmp_path / name for name in ("ingest", "archive", "outdated")}
        for folder in folders.values():
            folder.mkdir()
        config_file = tmp_path / "home" / "tabularium.toml"
        config_file.parent.mkdir()
        config_file.write_text(
            "[paths]\n" + "".join(f'{k} = "{v.as_posix()}"\n' for k, v in folders.items()),
            encoding="utf-8",
        )

        from tabularium import db
        from tabularium.config import load_config
        from tabularium.web.app import create_app

        config = load_config(config_file)
        config.database_path.parent.mkdir(parents=True, exist_ok=True)
        db.upgrade(db.sqlite_url(config.database_path))
        app = create_app(config, scan_on_startup=False)
        schema = app.openapi()
        app.state.outdated_store.dispose()

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(schema, indent=1, sort_keys=True) + "\n"
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(schema['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
