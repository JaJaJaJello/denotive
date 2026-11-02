@echo off
rem Rebuilds dist\DeNotive\DeNotive.exe. Run from this folder.
rem Optional argument: a different output folder (default "dist").
cd /d "%~dp0"
set OUT=%~1
if "%OUT%"=="" set OUT=dist
if /i "%OUT%"=="dist" (
    tasklist /FI "IMAGENAME eq DeNotive.exe" 2>nul | find /I "DeNotive.exe" >nul
    if not errorlevel 1 (
        echo DeNotive is running from this folder. Close it, then run build.bat again.
        exit /b 1
    )
)
if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install PySide6-Essentials python-pptx pillow numpy pyinstaller
)
if not exist assets\icon.ico .venv\Scripts\python.exe make_icon.py
.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name DeNotive --icon assets\icon.ico ^
    --distpath "%OUT%" --add-data "assets;assets" --collect-data pptx ^
    --exclude-module tkinter --exclude-module numpy --exclude-module PySide6.QtNetwork ^
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtOpenGL ^
    run.py
rem software OpenGL fallback and Qt translations are unused
del /q "%OUT%\DeNotive\_internal\PySide6\opengl32sw.dll" 2>nul
rmdir /s /q "%OUT%\DeNotive\_internal\PySide6\translations" 2>nul
rmdir /s /q build 2>nul
del /q DeNotive.spec 2>nul
echo.
echo Built: %~dp0%OUT%\DeNotive\DeNotive.exe
