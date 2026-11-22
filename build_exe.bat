@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV=.buildenv"
set "PYTHON=%VENV%\Scripts\python.exe"
if exist "%PYTHON%" goto :python_ready
where py.exe >nul 2>&1
if not errorlevel 1 (
  py.exe -m venv "%VENV%"
  if not errorlevel 1 goto :python_ready
)
where python.exe >nul 2>&1
if not errorlevel 1 (
  python.exe -m venv "%VENV%"
  if not errorlevel 1 goto :python_ready
)
echo ERROR: Could not create the build environment. Install Python 3.11 or later.
exit /b 1

:python_ready
"%PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
  echo ERROR: The .buildenv Python is invalid or older than 3.11.
  echo Install Python 3.11 or later, then remove .buildenv and try again.
  exit /b 1
)
"%PYTHON%" -m pip install -r requirements.txt "pyinstaller>=6.10,<7"
if errorlevel 1 (echo ERROR: Dependency installation failed. & exit /b 1)
"%PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onedir --name ZFTP --icon "assets\zftp_icon.ico" --add-data "assets\zftp_icon.png;assets" --collect-submodules pyftpdlib --collect-submodules paramiko --collect-submodules keyring main.py
if errorlevel 1 (echo ERROR: PyInstaller failed. & exit /b 1)
if not exist "dist\ZFTP\ZFTP.exe" (echo ERROR: EXE missing after build. & exit /b 1)
echo.
echo SUCCESS: %CD%\dist\ZFTP\ZFTP.exe
echo Important: Test EXE, FTP login, and transfers on Windows before distribution.
exit /b 0
