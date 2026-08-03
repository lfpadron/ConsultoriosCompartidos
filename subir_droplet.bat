@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%subir_droplet.ps1"

if not exist "%PS_SCRIPT%" (
    echo No encontre el script PowerShell:
    echo   "%PS_SCRIPT%"
    exit /b 1
)

if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -UseDefaults -RemoteDir "/opt/consultorios"
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*
)
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo El despliegue fallo con codigo %EXIT_CODE%.
)

exit /b %EXIT_CODE%
