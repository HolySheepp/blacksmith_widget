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
from PyQt5.QtWidgets import QApplication
from ui.widget import BlacksmithWidget


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    widget = BlacksmithWidget()
    widget.show()

    app.exec_()
    # os._exit() bypasses Python's atexit / thread-join machinery and guarantees
    # the process actually terminates.  Needed because pynput's Windows hook
    # threads can survive even after Listener.stop() is called in a frozen exe.
    os._exit(0)


if __name__ == "__main__":
    main()
