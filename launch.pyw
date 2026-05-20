"""
Double-click this file to launch the widget with NO console window.
Windows automatically uses pythonw.exe for .pyw files.
For development, you can still run:  python main.py
"""
import sys
import os

# Make sure imports resolve from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
main.main()
