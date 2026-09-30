param(
    [string]$Server = "143.198.166.39",
    [string]$User = "root",
    [string]$RemoteDir = "/opt/consultorios",
    [string]$ProjectDir = "",
    [string]$KeyPath = "",
    [string]$RepositoryUrl = "https://github.com/lfpadron/ConsultoriosCompartidos.git",
    [switch]$UseDefaults,
    [switch]$SkipExtract,
    [switch]$ExtractOnly,
    [switch]$SkipDeploy,
    [switch]$KeepArchive,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "Uso:"
    Write-Host "  .\subir_droplet.ps1"
    Write-Host "  .\subir_droplet.ps1 -UseDefaults"
    Write-Host "  .\subir_droplet.ps1 -Server 143.198.166.39 -User root"
    Write-Host ""
    Write-Host "Opciones utiles:"
    Write-Host "  -UseDefaults   No pregunta valores; usa los parametros/defaults."
    Write-Host "  -SkipExtract   Solo sube el paquete a /tmp; no lo extrae."
    Write-Host "  -ExtractOnly   Usa el paquete que ya existe en /tmp."
    Write-Host "  -SkipDeploy    Extrae archivos, pero no reconstruye ni migra."
    Write-Host "  -KeepArchive   Conserva el archivo .tar.gz local."
    Write-Host ""
    Write-Host "Requisito remoto:"
    Write-Host "  Debe existir $RemoteDir/.env con valores de produccion."
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

function Test-RepositoryOrigin {
    param(
        [string]$Directory,
        [string]$ExpectedUrl
    )

    if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
        Write-Warning "No encontre git; omito la validacion del repositorio local."
        return
    }

    $gitDirectory = Join-Path $Directory ".git"
    if (-not (Test-Path -LiteralPath $gitDirectory)) {
        Write-Warning "La carpeta local no contiene .git; omito la validacion del origen."
        return
    }

    $origin = (& git -C $Directory config --get remote.origin.url 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($origin)) {
        Write-Warning "No pude leer remote.origin.url."
        return
    }

    $originText = $origin.Trim()
    if ($originText -ne $ExpectedUrl) {
        Write-Warning "El origen local es '$originText'; se esperaba '$ExpectedUrl'."
    }

    $changes = & git -C $Directory status --short
    if ($LASTEXITCODE -eq 0 -and $changes) {
        Write-Warning "Hay cambios locales sin confirmar; se incluiran en el despliegue."
    }
}

if ($Help) {
    Show-Help
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = $ScriptDir
}
if ([string]::IsNullOrWhiteSpace($KeyPath)) {
    $KeyPath = Join-Path $ScriptDir "key_consultorios_dev"
}

if (-not $UseDefaults) {
    Write-Host ""
    Write-Host "Despliegue interactivo de Consultorios Compartidos"
    Write-Host "Puedes presionar Enter para aceptar cada valor."
    Write-Host ""
    $Server = Read-Default "IP o dominio del droplet" $Server
    $User = Read-Default "Usuario SSH" $User
    $RemoteDir = Read-Default "Carpeta destino remota" $RemoteDir
    $ProjectDir = Read-Default "Carpeta local del proyecto" $ProjectDir
    $KeyPath = Read-Default "Llave privada SSH" $KeyPath
}

$ProjectDir = [System.IO.Path]::GetFullPath($ProjectDir)
$KeyPath = [System.IO.Path]::GetFullPath($KeyPath)
$Remote = "$User@$Server"
$RemoteArchive = "/tmp/consultorios-compartidos-deploy.tar.gz"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LocalArchive = Join-Path ([System.IO.Path]::GetTempPath()) "consultorios-compartidos-deploy-$Timestamp.tar.gz"
$createdArchive = $false
$sshOptions = @(
    "-i", $KeyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15"
)

try {
    Test-Command "ssh"
    Test-Command "scp"
    Test-Command "tar"

    if (-not (Test-Path -LiteralPath $ProjectDir -PathType Container)) {
        throw "No existe la carpeta local del proyecto: $ProjectDir"
    }
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "No existe la llave privada SSH: $KeyPath"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "podman-compose.yml") -PathType Leaf)) {
        throw "No existe podman-compose.yml en: $ProjectDir"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "Dockerfile") -PathType Leaf)) {
        throw "No existe Dockerfile en: $ProjectDir"
    }
    if ($SkipExtract -and $ExtractOnly) {
        throw "No puedes usar -SkipExtract y -ExtractOnly al mismo tiempo."
    }

    Test-RepositoryOrigin -Directory $ProjectDir -ExpectedUrl $RepositoryUrl

    Write-Host ""
    Write-Host "Resumen:"
    Write-Host "  Repositorio: $RepositoryUrl"
    Write-Host "  Local:       $ProjectDir"
    Write-Host "  Remoto:      ${Remote}:$RemoteDir"
    Write-Host "  Llave:       $KeyPath"
    Write-Host ""
    Write-Host "Nota: si la llave tiene passphrase, SSH la pedira durante scp y ssh."

    if (-not $ExtractOnly) {
        $keyName = Split-Path -Leaf $KeyPath
        $excludePatterns = @(
            ".git",
            ".env",
            ".env.local",
            ".env.production",
            ".env.staging",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.sqlite3",
            "db.sqlite3",
            "media",
            "staticfiles",
            "tmp",
            "diff-*.txt",
            "*.pem",
            "*.key",
            $keyName,
            "$keyName.pub"
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

        $tarArgs = @("-czf", $LocalArchive)
        foreach ($pattern in $excludePatterns) {
            $tarArgs += @("--exclude", $pattern)
        }
        $tarArgs += @("-C", $ProjectDir, ".")

        Invoke-Native "Empaquetando codigo local" "tar" $tarArgs
        $createdArchive = $true

        Invoke-Native "Subiendo paquete a ${Remote}:$RemoteArchive" "scp" @(
            $sshOptions
            $LocalArchive
            "${Remote}:$RemoteArchive"
        )
    }
    else {
        Write-Host ""
        Write-Host "Modo ExtractOnly: usare el paquete remoto $RemoteArchive."
    }

    if (-not $SkipExtract) {
        $remoteDirQ = Quote-Sh $RemoteDir
        $remoteArchiveQ = Quote-Sh $RemoteArchive
        $remoteCommand = "set -e; mkdir -p $remoteDirQ; tar -xzf $remoteArchiveQ -C $remoteDirQ; rm -f $remoteArchiveQ; printf 'Archivos en destino: '; find $remoteDirQ -mindepth 1 -maxdepth 1 | wc -l"

        Invoke-Native "Extrayendo paquete en $RemoteDir" "ssh" @(
            $sshOptions
            $Remote
            $remoteCommand
        )
    }
    else {
        Write-Host ""
        Write-Host "SkipExtract activo: el paquete quedo en ${Remote}:$RemoteArchive"
    }

    if (-not $SkipExtract -and -not $SkipDeploy) {
        $remoteDirQ = Quote-Sh $RemoteDir
        $remoteDeploySteps = @(
            "set -e",
            "cd $remoteDirQ",
            "if [ ! -s podman-compose.yml ]; then echo 'podman-compose.yml no existe o esta vacio en $RemoteDir' >&2; exit 1; fi",
            "if [ ! -s .env ]; then echo '.env no existe o esta vacio en $RemoteDir' >&2; exit 1; fi",
            "command -v docker >/dev/null 2>&1 || { echo 'Docker no esta instalado' >&2; exit 1; }",
            "docker compose version >/dev/null",
            "export COMPOSE_BAKE=false",
            "docker compose -f podman-compose.yml config --quiet",
            "docker compose -f podman-compose.yml build web celery celery-beat",
            "docker compose -f podman-compose.yml up -d postgres redis minio",
            'attempt=0; until docker compose -f podman-compose.yml exec -T postgres sh -c ''pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'' >/dev/null 2>&1; do attempt=$((attempt+1)); if [ $attempt -ge 30 ]; then echo ''PostgreSQL no quedo listo a tiempo'' >&2; docker compose -f podman-compose.yml logs --tail=120 postgres; exit 1; fi; sleep 2; done',
            "docker compose -f podman-compose.yml run --rm web uv run python manage.py migrate --noinput",
            "docker compose -f podman-compose.yml run --rm web uv run python manage.py collectstatic --noinput",
            "docker compose -f podman-compose.yml up -d --force-recreate web celery celery-beat",
            "docker compose -f podman-compose.yml exec -T web uv run python manage.py check",
            "if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx; then systemctl reload nginx; fi",
            'if [ -n "$(docker compose -f podman-compose.yml port redis 6379 2>/dev/null || true)" ]; then echo ''Redis esta publicado en el host; corrige podman-compose.yml'' >&2; exit 1; fi',
            'if command -v ss >/dev/null 2>&1 && ss -lntH | awk ''{print $4}'' | grep -Eq ''^(0\.0\.0\.0:6379|\*:6379|:::6379|\[::\]:6379)$''; then echo ''Redis escucha publicamente en 6379. Revisa Redis del host y el firewall.'' >&2; exit 1; fi',
            'if command -v curl >/dev/null 2>&1; then attempt=0; until curl -fsS http://127.0.0.1:8000/login/ >/dev/null; do attempt=$((attempt+1)); if [ $attempt -ge 30 ]; then echo ''La aplicacion no respondio en http://127.0.0.1:8000/login/'' >&2; docker compose -f podman-compose.yml logs --tail=120 web; exit 1; fi; sleep 2; done; fi',
            "docker compose -f podman-compose.yml ps"
        )
        $remoteDeployCommand = $remoteDeploySteps -join "; "

        Invoke-Native "Reconstruyendo contenedores y aplicando migraciones" "ssh" @(
            $sshOptions
            $Remote
            $remoteDeployCommand
        )
    }
    elseif ($SkipDeploy) {
        Write-Host ""
        Write-Host "SkipDeploy activo: no se reconstruyeron contenedores ni se aplicaron migraciones."
    }

    Write-Host ""
    Write-Host "Despliegue completado correctamente."
    Write-Host "Aplicacion: http://${Server}:8000/"
}
catch {
    Write-Host ""
    Write-Host "Fallo el despliegue: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($createdArchive -and -not $KeepArchive -and (Test-Path -LiteralPath $LocalArchive)) {
        Remove-Item -LiteralPath $LocalArchive -Force
    }
    elseif ($createdArchive -and $KeepArchive) {
        Write-Host "Paquete local conservado: $LocalArchive"
    }
}
