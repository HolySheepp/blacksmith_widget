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
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from ui.widget import BlacksmithWidget


def main():
    # Must be set BEFORE QApplication is created.
    # Normalises all coordinates to logical pixels so geometry and fonts
    # both scale with DPR; prevents the "anvil tiny / text huge" mismatch
    # that happens when Qt mixes physical and logical units on high-DPI screens.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # we call quit() explicitly in closeEvent

    # Wire os._exit to aboutToQuit so the process always dies when quit() is
    # called — regardless of whether any dialog/thread blocks exec_() from returning.
    app.aboutToQuit.connect(lambda: os._exit(0))

    widget = BlacksmithWidget()
    widget.show()

    app.exec_()
    os._exit(0)   # fallback: should not be reached, but guarantees termination


if __name__ == "__main__":
    main()
