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


def download_exe(url: str, dest: str, progress_cb=None) -> bool:
    """
    Stream-download url → dest with an optional progress callback.
    progress_cb(pct: int) is called with 0-100 as data arrives.
    30 s connection timeout.  Cleans up partial file on failure.
    Returns True on success.
    """
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done  = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65_536)   # 64 KB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(int(done * 100 / total))
        if progress_cb:
            progress_cb(100)
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
    Write _bsw_updater.bat to %TEMP% (guaranteed ASCII path) and launch it
    hidden.  The bat waits for the old process to exit, retries the move
    until it succeeds, then relaunches the exe and deletes itself.
    The caller must call self.close() immediately after this.
    """
    import tempfile
    bat = os.path.join(tempfile.gettempdir(), "_bsw_updater.bat")

    # utf-8-sig (BOM) + chcp 65001 lets cmd.exe handle paths that contain
    # Chinese or other non-ASCII characters.
    content = (
        "@echo off\n"
        "chcp 65001 >nul\n"
        "timeout /t 5 /nobreak >nul\n"
        ":retry\n"
        f'move /y "{new_exe}" "{old_exe}" >nul 2>&1\n'
        "if errorlevel 1 (\n"
        "    timeout /t 2 /nobreak >nul\n"
        "    goto retry\n"
        ")\n"
        # Unblock the downloaded file (removes Zone.Identifier / SmartScreen flag)
        f'powershell -Command "Unblock-File -LiteralPath \'{old_exe}\'" >nul 2>&1\n'
        # Use PowerShell Start-Process instead of cmd's "start" so the new exe
        # inherits a proper long-path environment (avoids FRANCI~1 short-name
        # issue that breaks PyInstaller's python DLL search at launch time).
        f'powershell -Command "Start-Process -FilePath \'{old_exe}\'" >nul 2>&1\n'
        'del "%~f0"\n'
    )
    with open(bat, "w", encoding="utf-8-sig") as f:
        f.write(content)

    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
