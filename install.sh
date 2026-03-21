#!/bin/bash
echo "============================================"
echo "  Denuker — Discord Backup & Recovery Tool"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: Python 3 not found."
  echo "Download it from: https://www.python.org/downloads/"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PY_VER detected."

# Check tkinter (built into python.org distributions, but missing from some Homebrew builds)
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo ""
  echo "WARNING: tkinter (GUI) is not available in this Python installation."
  echo ""
  echo "Fixes:"
  echo "  macOS (Homebrew):  brew install python-tk"
  echo "  macOS (preferred): Download Python from https://www.python.org/downloads/"
  echo "  Linux:             sudo apt install python3-tk  (Debian/Ubuntu)"
  echo "                     sudo dnf install python3-tkinter  (Fedora)"
  echo ""
  echo "After fixing tkinter, run this script again."
  exit 1
fi

echo "tkinter OK"
echo ""
echo "Installing dependencies..."
pip3 install -r requirements.txt

echo ""
echo "============================================"
echo "  Installation complete!"
echo "  Run the app:  python3 denuker.py"
echo "            or: ./run.sh"
echo "============================================"
