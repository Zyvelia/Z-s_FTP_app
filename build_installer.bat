@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "dist\ZFTP\ZFTP.exe" (
  echo ERROR: EXE not built. Run build_exe.bat first.
  exit /b 1
)
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  where ISCC.exe >nul 2>&1
  if errorlevel 1 (echo ERROR: Inno Setup 6 missing. Install it first. & exit /b 1)
  set "ISCC=ISCC.exe"
)
"%ISCC%" "packaging\ZFTP.iss"
if errorlevel 1 (echo ERROR: Installer compilation failed. & exit /b 1)
if not exist "installer_output\ZFTP_Setup_v2.1.7.exe" (echo ERROR: Installer output missing. & exit /b 1)
echo.
echo SUCCESS: %CD%\installer_output\ZFTP_Setup_v2.1.7.exe
exit /b 0
