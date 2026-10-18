"""Hand files to Windows: open with the default app, show in Explorer.

The server runs on the user's own machine, so these act on the user's desktop.
"""

import os
import subprocess
from pathlib import Path

from tabularium.fsutil import IS_WINDOWS, strip_lp


class DesktopError(Exception):
    pass


# Opening these would run them. Copies also lose the "downloaded from the internet" marker
# that normally makes Windows warn, so they are never opened from the app.
RUNNABLE_EXTENSIONS = frozenset(
    {
        ".exe",
        ".com",
        ".scr",
        ".pif",
        ".cpl",
        ".msi",
        ".msp",
        ".msc",
        ".bat",
        ".cmd",
        ".ps1",
        ".psm1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".hta",
        ".lnk",
        ".url",
        ".reg",
        ".jar",
        ".appref-ms",
        ".application",
        ".gadget",
        ".inf",
        ".scf",
        ".settingcontent-ms",
        ".library-ms",
        ".chm",
    }
)


def is_runnable(filename: str) -> bool:
    return Path(filename).suffix.lower() in RUNNABLE_EXTENSIONS


def open_with_default_app(path: Path) -> None:
    if not IS_WINDOWS:
        raise DesktopError("Opening files is only supported on Windows.")
    os.startfile(strip_lp(str(path)))  # noqa: S606 — the path comes from our own archive


def show_in_explorer(path: Path) -> None:
    if not IS_WINDOWS:
        raise DesktopError("Showing files is only supported on Windows.")
    # explorer.exe returns 1 even on success, so the exit code is not checked.
    subprocess.Popen(["explorer", f"/select,{strip_lp(str(path))}"])  # noqa: S603, S607
