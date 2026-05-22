"""
Entry point for the Blacksmith desktop widget.

Usage:
    pip install PyQt5 pynput
    python main.py

Right-click the widget to toggle mode or quit.
Left-click-drag to reposition.

Emergency rescue flag (run from a command prompt):
    BlacksmithWidget.exe --remove-autostart
Removes the Windows autostart registry entry and exits immediately.
Use this if the game is stuck at startup and the machine reboots into it
repeatedly, preventing you from deleting the exe.
"""
import os
import sys
import pathlib
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from ui.widget import BlacksmithWidget

# ── Startup diagnostic log ────────────────────────────────────────────────────
# Written to %APPDATA%\BlacksmithWidget\startup.log so it survives even when
# the window never appears.  Opened fresh (overwrite) each launch.

_LOG_PATH: pathlib.Path | None = None


def _slog(msg: str) -> None:
    """Append one line to the startup log (best-effort, never raises)."""
    global _LOG_PATH
    try:
        if _LOG_PATH is None:
            _LOG_PATH = (pathlib.Path(os.environ["APPDATA"])
                         / "BlacksmithWidget" / "startup.log")
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _remove_autostart() -> None:
    """Remove the Windows autostart registry entry and exit.
    Called when the user passes --remove-autostart on the command line.
    This is a rescue path: it must not import Qt or start a GUI."""
    import winreg
    _REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _REG_NAME = "BlacksmithWidget"
    removed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, _REG_NAME)
                removed = True
            except FileNotFoundError:
                pass  # already gone
    except Exception as e:
        print(f"[remove-autostart] registry error: {e}", file=sys.stderr)
        sys.exit(1)

    if removed:
        print("[remove-autostart] Autostart entry removed successfully.")
    else:
        print("[remove-autostart] No autostart entry found (already removed).")
    sys.exit(0)


def main():
    # ── Emergency rescue: --remove-autostart ──────────────────────────────────
    # Must be checked BEFORE any Qt import or GUI code runs, so that even a
    # machine where the game hangs on startup can be rescued from the command line.
    if "--remove-autostart" in sys.argv:
        _remove_autostart()

    # Must be set BEFORE QApplication is created.
    # Normalises all coordinates to logical pixels so geometry and fonts
    # both scale with DPR; prevents the "anvil tiny / text huge" mismatch
    # that happens when Qt mixes physical and logical units on high-DPI screens.
    # Open the startup log fresh (overwrite) for this launch.
    try:
        _LOG_PATH_init = (pathlib.Path(os.environ["APPDATA"])
                          / "BlacksmithWidget" / "startup.log")
        _LOG_PATH_init.parent.mkdir(parents=True, exist_ok=True)
        _LOG_PATH_init.write_text("", encoding="utf-8")   # truncate / create
        global _LOG_PATH
        _LOG_PATH = _LOG_PATH_init
        _slog(f"[startup] log opened: {_LOG_PATH}")
    except Exception:
        pass

    _slog("[startup] BlacksmithWidget importing OK")

    _slog("[startup] setAttribute HiDPI")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

    _slog("[startup] QApplication()")
    app = QApplication(sys.argv)
    _slog("[startup] QApplication OK")

    app.setQuitOnLastWindowClosed(False)   # we call quit() explicitly in closeEvent

    # Wire os._exit to aboutToQuit so the process always dies when quit() is
    # called — regardless of whether any dialog/thread blocks exec_() from returning.
    app.aboutToQuit.connect(lambda: os._exit(0))

    _slog("[startup] BlacksmithWidget()")
    widget = BlacksmithWidget()
    _slog("[startup] BlacksmithWidget init OK")
    _slog("[startup] widget.show() — mapping HWND to screen")
    widget.show()
    _slog("[startup] widget.show() OK")

    app.exec_()
    os._exit(0)   # fallback: should not be reached, but guarantees termination


if __name__ == "__main__":
    main()
