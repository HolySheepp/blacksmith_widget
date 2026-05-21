"""
Entry point for the Blacksmith desktop widget.

Usage:
    pip install PyQt5 pynput
    python main.py

Right-click the widget to toggle mode or quit.
Left-click-drag to reposition.
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


def main():
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
    widget.show()
    _slog("[startup] widget.show() OK")

    app.exec_()
    os._exit(0)   # fallback: should not be reached, but guarantees termination


if __name__ == "__main__":
    main()
