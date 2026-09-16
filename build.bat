@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name PdfEditor pdf_editor.py
echo.
echo Done: dist\PdfEditor.exe
pause
