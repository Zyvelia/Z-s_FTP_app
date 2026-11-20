@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-ZFTP.ps1" %*
set "UPDATE_RESULT=%ERRORLEVEL%"
pause
exit /b %UPDATE_RESULT%
