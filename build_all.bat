@echo off
setlocal EnableExtensions
set "ZFTP_PAUSE=1"
if /i "%~1"=="--no-pause" set "ZFTP_PAUSE="
set "ZFTP_BUILD_EXIT=1"

cd /d "%~dp0"
if errorlevel 1 goto :source_missing
if not exist "main.py" goto :source_missing
if not exist "build_exe.bat" goto :source_missing
if not exist "build_installer.bat" goto :source_missing
if not exist "packaging\ZFTP.iss" goto :source_missing

echo Checking that Inno Setup is available...
call build_installer.bat --check
if errorlevel 1 goto :finish

echo.
echo [1/2] Building Z FTP...
call build_exe.bat
if errorlevel 1 (
  echo ERROR: The app build failed. See the Python or PyInstaller error above.
  goto :finish
)

echo.
echo [2/2] Compiling the Windows installer...
call build_installer.bat
if errorlevel 1 (
  echo ERROR: The installer build failed. See the Inno Setup error above.
  goto :finish
)

set "ZFTP_BUILD_EXIT=0"
echo.
echo EXE and installer build completed.
goto :finish

:source_missing
echo ERROR: Extract the entire Z FTP source ZIP and run build_all.bat from that folder.

:finish
if defined ZFTP_PAUSE (
  echo.
  pause
)
exit /b %ZFTP_BUILD_EXIT%
