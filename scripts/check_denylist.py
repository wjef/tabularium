"""Block commits that contain personal words (see docs/adr/0006).

The word list lives OUTSIDE the repo: ``%LOCALAPPDATA%\\Tabularium\\denylist.txt``
(or ``$TABULARIUM_DENYLIST``). One word per line, ``#`` starts a comment. Matching is
case- and diacritic-insensitive substring matching.

Usage (wired up by pre-commit):
    check_denylist.py FILE...                 check names and contents of staged files
    check_denylist.py --commit-msg MSGFILE    check the commit message

Self-contained on purpose: the hook must work without the package being importable.
"""

import argparse
import os
import sys
import unicodedata
from pathlib import Path

_EXTRA = str.maketrans({"ł": "l", "Ł": "l", "ß": "ss", "đ": "d", "ø": "o"})
_MAX_BYTES = 5_000_000


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_EXTRA))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def denylist_path() -> Path:
    override = os.environ.get("TABULARIUM_DENYLIST")
    if override:
        return Path(override)
    base = os.environ.get("TABULARIUM_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")), "Tabularium"
    )
    return Path(base) / "denylist.txt"


def load_words(path: Path) -> list[str]:
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            words.append(normalize(line))
    return words


def find_hits(text: str, words: list[str]) -> list[tuple[int, str]]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        norm = normalize(line)
        hits.extend((lineno, word) for word in words if word in norm)
    return hits


def read_text(path: Path) -> str | None:
    """File content as text, or None for binary / oversized files."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > _MAX_BYTES or b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def strip_git_comments(message: str) -> str:
    return "\n".join(line for line in message.splitlines() if not line.startswith("#"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-msg", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    path = denylist_path()
    if not path.is_file():
        print(f"WARNING: denylist not found at {path} - personal-word check skipped.")
        return 0
    words = load_words(path)
    if not words:
        return 0

    problems = []
    for name in args.files:
        file = Path(name)
        if args.commit_msg:
            text = strip_git_comments(file.read_text(encoding="utf-8", errors="replace"))
            problems += [f"commit message:{n}: contains '{w}'" for n, w in find_hits(text, words)]
            continue
        problems += [f"{name}: file name contains '{w}'" for _, w in find_hits(name, words)]
        text = read_text(file)
        if text is not None:
            problems += [f"{name}:{n}: contains '{w}'" for n, w in find_hits(text, words)]

    if problems:
        print("Commit blocked - personal words from the denylist found:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
