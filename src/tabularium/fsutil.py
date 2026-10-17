"""Low-level file system helpers for Windows specifics (long paths, locks, placeholders)."""

import os
import stat
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# Windows file attributes (not all are exposed by the stat module)
_RECALL_ON_OPEN = 0x0004_0000
_RECALL_ON_DATA_ACCESS = 0x0040_0000
_PLACEHOLDER_ATTRS = stat.FILE_ATTRIBUTE_OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA_ACCESS
_HIDDEN_ATTRS = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


def lp(path: Path | str) -> str:
    """Path string usable beyond 260 characters on Windows (``\\\\?\\`` prefix)."""
    text = str(path)
    if not IS_WINDOWS or text.startswith("\\\\?\\"):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):  # UNC share: \\server\share → \\?\UNC\server\share
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def strip_lp(text: str) -> str:
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def is_placeholder(st: os.stat_result) -> bool:
    """OneDrive Files On-Demand placeholder whose content is only in the cloud."""
    return bool(getattr(st, "st_file_attributes", 0) & _PLACEHOLDER_ATTRS)


def is_hidden_or_system(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_ATTRS)


def is_locked(path: Path | str) -> bool:
    """True if another process has the file open (Windows: exclusive open fails)."""
    if not IS_WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    generic_read, open_existing, share_none = 0x8000_0000, 3, 0
    handle = create_file(lp(path), generic_read, share_none, None, open_existing, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        return True
    ctypes.windll.kernel32.CloseHandle(handle)
    return False


def unique_destination(folder: Path, name: str) -> Path:
    """``folder/name``, or ``folder/stem (n).ext`` if that already exists."""
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while os.path.lexists(lp(candidate)):
        candidate = folder / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def move_file(src: Path, dst_folder: Path) -> Path:
    """Move ``src`` into ``dst_folder`` without overwriting anything; returns the new path."""
    os.makedirs(lp(dst_folder), exist_ok=True)
    dst = unique_destination(dst_folder, src.name)
    os.rename(lp(src), lp(dst))  # rename never overwrites on Windows
    return dst
