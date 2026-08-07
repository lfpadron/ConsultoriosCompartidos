param(
    [string]$Server = "143.198.166.39",
    [string]$User = "root",
    [string]$RemoteDir = "/opt/consultorios",
    [string]$KeyPath = "",
    [string]$ComposeFile = "podman-compose.yml",
    [string]$SiteUrl = "https://tu-consultorio.com.mx/",
    [switch]$UseDefaults,
    [switch]$SkipPull,
    [switch]$SkipMigrate,
    [switch]$SkipCollectStatic,
    [switch]$SkipRestartWeb,
    [switch]$SkipNginxReload,
    [switch]$RunTests,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "Uso:"
    Write-Host "  .\subir_droplet.ps1"
    Write-Host "  .\subir_droplet.ps1 -UseDefaults"
    Write-Host "  .\subir_droplet.ps1 -Server 143.198.166.39 -User root -RemoteDir /opt/consultorios"
    Write-Host ""
    Write-Host "Opciones utiles:"
    Write-Host "  -UseDefaults        No pregunta valores; usa los parametros/defaults."
    Write-Host "  -RunTests           Ejecuta pytest local antes de desplegar."
    Write-Host "  -SkipPull           No ejecuta git pull en el droplet."
    Write-Host "  -SkipMigrate        No ejecuta manage.py migrate."
    Write-Host "  -SkipCollectStatic  No ejecuta collectstatic."
    Write-Host "  -SkipRestartWeb     No reinicia el contenedor web."
    Write-Host "  -SkipNginxReload    No recarga nginx."
}

function Read-Default {
    param(
        [string]$Label,
        [string]$Default
    )

    $value = Read-Host "$Label [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }

    return $value.Trim()
}

function Test-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "No encontre '$Name' en PATH."
    }
}

function Invoke-Native {
    param(
        [string]$Description,
        [string]$Exe,
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "==> $Description"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description fallo con codigo $LASTEXITCODE."
    }
}

function Quote-Sh {
    param([string]$Value)

    return "'" + ($Value -replace "'", "'\''") + "'"
}

if ($Help) {
    Show-Help
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($KeyPath)) {
    $defaultKeyPath = Join-Path $ScriptDir "key_consultorios_dev"
    if (Test-Path -LiteralPath $defaultKeyPath -PathType Leaf) {
        $KeyPath = $defaultKeyPath
    }
}

if (-not $UseDefaults) {
    Write-Host ""
    Write-Host "Despliegue interactivo al droplet de Consultorios Compartidos"
    Write-Host "Puedes presionar Enter para aceptar cada valor."
    Write-Host ""
    $Server = Read-Default "IP o dominio del droplet" $Server
    $User = Read-Default "Usuario SSH" $User
    $RemoteDir = Read-Default "Carpeta del proyecto en el droplet" $RemoteDir
    $KeyPath = Read-Default "Llave privada SSH" $KeyPath
    $ComposeFile = Read-Default "Archivo compose" $ComposeFile
    $SiteUrl = Read-Default "URL publica para smoke test" $SiteUrl
}

if ([string]::IsNullOrWhiteSpace($RemoteDir)) {
    $RemoteDir = "/opt/consultorios"
}
else {
    $RemoteDir = $RemoteDir.TrimEnd("/")
}

if (-not [string]::IsNullOrWhiteSpace($KeyPath)) {
    $KeyPath = [System.IO.Path]::GetFullPath($KeyPath)
}

$Remote = "$User@$Server"

try {
    Test-Command "ssh"

    if (-not [string]::IsNullOrWhiteSpace($KeyPath) -and -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "No existe la llave privada SSH: $KeyPath"
    }

    Write-Host ""
    Write-Host "Resumen:"
    Write-Host "  Remoto:  ${Remote}:$RemoteDir"
    Write-Host "  Compose: $ComposeFile"
    if (-not [string]::IsNullOrWhiteSpace($KeyPath)) {
        Write-Host "  Llave:   $KeyPath"
    }
    else {
        Write-Host "  Llave:   SSH usara su configuracion por defecto"
    }
    Write-Host "  URL:     $SiteUrl"

    if ($RunTests) {
        Invoke-Native "Ejecutando pruebas locales" "uv" @("run", "pytest")
    }

    $remoteDirQ = Quote-Sh $RemoteDir
    $composeFileQ = Quote-Sh $ComposeFile
    $remoteDeploySteps = @(
        "set -e",
        "cd $remoteDirQ",
        "if [ ! -s $composeFileQ ]; then echo '$ComposeFile no existe o esta vacio en $RemoteDir' >&2; exit 1; fi",
        "if [ ! -s .env ]; then echo '.env no existe o esta vacio en $RemoteDir' >&2; exit 1; fi",
        "systemctl enable docker >/dev/null 2>&1 || true",
        "systemctl start docker >/dev/null 2>&1 || true"
    )

    if (-not $SkipPull) {
        $remoteDeploySteps += "git pull"
    }

    $remoteDeploySteps += "docker compose -f $composeFileQ up -d --build"

    if (-not $SkipMigrate) {
        $remoteDeploySteps += "docker compose -f $composeFileQ exec -T web uv run python manage.py migrate"
    }

    if (-not $SkipCollectStatic) {
        $remoteDeploySteps += "docker compose -f $composeFileQ exec -T web uv run python manage.py collectstatic --noinput"
    }

    if (-not $SkipRestartWeb) {
        $remoteDeploySteps += "docker compose -f $composeFileQ restart web"
        $remoteDeploySteps += "docker compose -f $composeFileQ up -d web"
        $remoteDeploySteps += "sleep 8"
    }

    if (-not $SkipNginxReload) {
        $remoteDeploySteps += "systemctl reload nginx"
    }

    $remoteDeploySteps += "docker compose -f $composeFileQ ps"
    $remoteDeploySteps += "curl -fsSIL --max-time 10 http://127.0.0.1:8000/ >/dev/null || echo 'Aviso: Django no respondio localmente despues del despliegue; si el navegador muestra 502 revisa logs de web.' >&2"
    $remoteDeploySteps += "curl -fsSIL --max-time 15 $(Quote-Sh $SiteUrl) >/dev/null || echo 'Aviso: la URL publica no respondio correctamente; revisa nginx/DNS si el navegador falla.' >&2"

    $remoteDeployCommand = $remoteDeploySteps -join "; "
    $sshArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($KeyPath)) {
        $sshArgs += @("-i", $KeyPath)
    }
    $sshArgs += @("-o", "StrictHostKeyChecking=accept-new", $Remote, $remoteDeployCommand)

    Invoke-Native "Desplegando en $RemoteDir" "ssh" $sshArgs

    Write-Host ""
    Write-Host "Despliegue completado correctamente."
    if (-not [string]::IsNullOrWhiteSpace($SiteUrl)) {
        Write-Host "URL publica: $SiteUrl"
    }
}
catch {
    Write-Host ""
    Write-Host "Fallo el despliegue: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
