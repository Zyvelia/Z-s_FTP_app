@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "ISCC="
if defined ZFTP_ISCC (
  if not exist "%ZFTP_ISCC%" (
    echo ERROR: ZFTP_ISCC does not point to an existing ISCC.exe.
    exit /b 1
  )
  set "ISCC=%ZFTP_ISCC%"
)

if not defined ISCC for %%I in (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do if not defined ISCC if exist "%%~I" set "ISCC=%%~I"

if not defined ISCC for %%H in (HKCU HKLM) do for %%B in (32 64) do (
  for /f "tokens=2,*" %%T in ('reg query "%%H\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v InstallLocation /reg:%%B 2^>nul') do (
    if not defined ISCC if /i "%%T"=="REG_SZ" if exist "%%U\ISCC.exe" set "ISCC=%%U\ISCC.exe"
  )
)

if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do (
  if not defined ISCC set "ISCC=%%I"
)
if not defined ISCC (
  echo ERROR: Could not find ISCC.exe. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
  echo For a custom install, set ZFTP_ISCC to the full path to ISCC.exe.
  exit /b 1
)
if /i "%~1"=="--check" (
  echo Found Inno Setup compiler.
  exit /b 0
)

if not exist "dist\ZFTP\ZFTP.exe" (
  echo ERROR: EXE not built. Run build_exe.bat first.
  exit /b 1
)
"%ISCC%" "packaging\ZFTP.iss"
if errorlevel 1 (echo ERROR: Installer compilation failed. & exit /b 1)
if not exist "installer_output\ZFTP_Setup_v2.1.12.exe" (echo ERROR: Installer output missing. & exit /b 1)
echo.
echo SUCCESS: %CD%\installer_output\ZFTP_Setup_v2.1.12.exe
exit /b 0
