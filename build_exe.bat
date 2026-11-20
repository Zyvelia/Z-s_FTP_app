@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV=.buildenv"
set "PYTHON=%VENV%\Scripts\python.exe"
if not exist "%PYTHON%" (
  where py >nul 2>&1
  if errorlevel 1 (echo ERROR: Python launcher not found. Install Python 3.11 or newer. & exit /b 1)
  py -m venv "%VENV%"
  if errorlevel 1 exit /b 1
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
