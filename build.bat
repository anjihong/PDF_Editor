@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 exit /b 1
rem Keep third-party DLLs from the caller's PATH out of PyInstaller's dependency scan.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --name PdfEditor --icon assets\app.ico --add-data "assets\app.ico;assets" --add-data "updater.ps1;." --additional-hooks-dir hooks --exclude-module PySide6.QtPdf --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtVirtualKeyboard pdf_editor.py
if errorlevel 1 exit /b 1
if not exist dist\PdfEditor.exe exit /b 1
echo.
echo Done: dist\PdfEditor.exe
if /i not "%~1"=="--no-pause" pause
