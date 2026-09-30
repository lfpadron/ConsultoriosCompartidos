@echo off
setlocal

cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0subir_droplet.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo Proceso terminado correctamente.
) else (
    echo El proceso fallo con codigo %EXITCODE%.
)
echo.
pause
exit /b %EXITCODE%
