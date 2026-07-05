@echo off
setlocal
cd /d "%~dp0"
uv run python tools\control_tui.py
