@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q -r requirements.txt
rem Keep third-party DLLs from the caller's PATH out of PyInstaller's dependency scan.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --name PdfEditor --icon assets\app.ico --add-data "assets;assets" pdf_editor.py
echo.
echo Done: dist\PdfEditor.exe
pause
