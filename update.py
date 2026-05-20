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
    Write _bsw_updater.bat to %TEMP% and launch it via ShellExecuteW.

    Root cause of the "Failed to load Python DLL" error on auto-launch:
      %TEMP% on this machine resolves to the 8.3 short path (FRANCI~1).
      All processes spawned from our PyInstaller exe inherit a polluted
      environment (stale _MEI path in PATH, short-form TEMP, etc.).
      LoadLibrary fails for DLLs extracted to a short-name path in some
      Windows/AV configurations — even though double-click works fine.

    Fix: use Windows Task Scheduler to launch the new exe.
      The scheduler service (svchost.exe) creates the process completely
      outside our chain, with a fresh environment sourced from the user's
      profile — identical to what Explorer provides on a double-click.

    The caller must call self.close() immediately after this.
    """
    import ctypes
    import tempfile
    bat = os.path.join(tempfile.gettempdir(), "_bsw_updater.bat")

    # utf-8-sig (BOM) + chcp 65001 lets cmd.exe handle paths that contain
    # Chinese or other non-ASCII characters.
    #
    # The PowerShell one-liner:
    #   • Registers a one-shot task named _BSW_Update that fires in 4 s.
    #   • Task Scheduler launches the exe independently of our process tree.
    #   • The task is auto-deleted 5 min after it expires.
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
        # Strip Zone.Identifier so SmartScreen won't block the launch
        f'powershell -Command "Unblock-File -LiteralPath \'{old_exe}\'" >nul 2>&1\n'
        # Create an interactive scheduled task, then fire it immediately with
        # /run — this bypasses trigger-time scheduling entirely so the exe
        # launches the moment the bat finishes, not after a polling delay.
        # /it = "interactive task": runs in the user's active desktop session.
        # \"...\" inside the outer "" quotes the exe path for schtasks /tr.
        f'schtasks /create /f /tn "_BSW_Update" /tr "\\"{old_exe}\\"" /sc once /st 00:00 /it >nul 2>&1\n'
        'schtasks /run /tn "_BSW_Update" >nul 2>&1\n'
        'del "%~f0"\n'
    )
    with open(bat, "w", encoding="utf-8-sig") as f:
        f.write(content)

    # Use ShellExecuteW so the bat itself also has a proper interactive context.
    # SW_HIDE (0) suppresses the console window.
    ret = ctypes.windll.shell32.ShellExecuteW(None, "open", bat, None, None, 0)
    if ret <= 32:
        # Fallback if ShellExecute is unavailable
        subprocess.Popen(
            ["cmd", "/c", bat],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
