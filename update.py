"""
Auto-update helper — queries GitHub Releases API, downloads new exe,
then launches a small bat that swaps files after the old process exits.

Only meaningful when running as a frozen PyInstaller exe.
All network/IO is designed to fail silently so a bad connection never
breaks the game.
"""
import json
import os
import subprocess
import sys
import urllib.request

_API_URL    = "https://api.github.com/repos/HolySheepp/blacksmith_widget/releases/latest"
_ASSET_NAME = "BlacksmithWidget.exe"
_HEADERS    = {"User-Agent": "BlacksmithWidget-Updater/1.0"}


# ── Version helpers ───────────────────────────────────────────────────────────

def _parse(tag: str) -> tuple:
    """'v0.0.6' → (0, 0, 6).  Returns (0,) on any parse error."""
    try:
        return tuple(int(x) for x in tag.lstrip("vV").split("."))
    except Exception:
        return (0,)


def is_newer(remote_tag: str, local_version: str) -> bool:
    return _parse(remote_tag) > _parse(local_version)


# ── Network ───────────────────────────────────────────────────────────────────

def fetch_latest(timeout: int = 6) -> dict | None:
    """
    Call GitHub Releases API.
    Returns {"tag": str, "url": str} when the latest release contains
    BlacksmithWidget.exe, or None on any error (no internet, private repo, etc.).
    """
    try:
        req = urllib.request.Request(_API_URL, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        for asset in data.get("assets", []):
            if asset.get("name") == _ASSET_NAME:
                return {"tag": tag, "url": asset["browser_download_url"]}
    except Exception:
        pass
    return None


def download_exe(url: str, dest: str) -> bool:
    """
    Download url → dest (blocking).
    Cleans up partial file on failure.  Returns True on success.
    """
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        return False


# ── Exe swap ──────────────────────────────────────────────────────────────────

def exe_path() -> str:
    """Full path of the running frozen exe."""
    return sys.executable


def launch_updater(new_exe: str, old_exe: str) -> None:
    """
    Write _bsw_updater.bat next to old_exe, then launch it hidden.
    The bat waits ~3 s for the old process to exit, moves new_exe over
    old_exe, launches it, and deletes itself.
    The caller must call self.close() immediately after this.
    """
    bat = os.path.join(os.path.dirname(old_exe), "_bsw_updater.bat")
    with open(bat, "w", encoding="ascii") as f:
        f.write("@echo off\n")
        f.write("timeout /t 3 /nobreak >nul\n")
        f.write(f'move /y "{new_exe}" "{old_exe}"\n')
        f.write(f'start "" "{old_exe}"\n')
        f.write('del "%~f0"\n')
    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
