@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name PdfEditor --icon assets\app.ico --add-data "assets;assets" pdf_editor.py
echo.
echo Done: dist\PdfEditor.exe
pause
