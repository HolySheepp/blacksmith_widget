"""
Persistent save/load — stored in %APPDATA%\\BlacksmithWidget\\save.json
(like most games on Windows).
Falls back to the exe/script directory if AppData is unavailable.
"""
import json
import os
import sys


def _save_dir() -> str:
    # %APPDATA%  →  C:\Users\<name>\AppData\Roaming
    appdata = os.environ.get("APPDATA")
    if appdata:
        folder = os.path.join(appdata, "BlacksmithWidget")
    else:
        # Fallback: next to exe / script
        if getattr(sys, "frozen", False):
            folder = os.path.dirname(sys.executable)
        else:
            folder = os.path.dirname(os.path.abspath(__file__))
    return folder


def save_path() -> str:
    return os.path.join(_save_dir(), "save.json")


def load_save() -> dict:
    """Return saved data dict, or {} on first run / any error."""
    try:
        with open(save_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def write_save(data: dict) -> None:
    """Write data to AppData save file (creates directory if needed)."""
    try:
        os.makedirs(_save_dir(), exist_ok=True)
        with open(save_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
