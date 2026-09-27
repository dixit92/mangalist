@echo off
REM Build script for the MangaList Windows app (PyInstaller onedir build).
REM Output: dist\MangaList\MangaList.exe plus dist\MangaList\_internal\
REM The installer and the portable zip are made by CI (.github\workflows\build.yml);
REM to make the installer locally, install Inno Setup 6 and run:
REM   iscc /DAppVersion=0.0.0 packaging\windows\MangaList.iss

setlocal enabledelayedexpansion

echo ===========================================
echo Building MangaList
echo ===========================================
echo.

REM Check if we're in a virtual environment
if "%VIRTUAL_ENV%"=="" (
    echo WARNING: No virtual environment detected.
    echo It's recommended to use a venv: python -m venv .venv
    echo.
)

REM Install/update requirements
echo Installing dependencies...
pip install -r requirements-dev.txt --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    exit /b 1
)

REM Clean previous builds
echo Cleaning previous builds...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM Icons for the exe and the installer
python packaging\make_icon.py
if errorlevel 1 (
    echo ERROR: Icon generation failed
    exit /b 1
)

REM Build with PyInstaller
echo Building with PyInstaller...
pyinstaller MangaList.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

REM Verify output
if exist "dist\MangaList\MangaList.exe" (
    echo.
    echo ===========================================
    echo Build successful!
    echo Output: dist\MangaList\MangaList.exe
    echo ===========================================
) else (
    echo ERROR: Expected output file not found
    exit /b 1
)

endlocal
