<#
.SYNOPSIS
    Überträgt das lokale KFZ-Schnäppchen-Add-on nach Home Assistant OS.

.DESCRIPTION
    Kopiert den geprüften Build-Kontext nach /addons/kfz_schnaeppchen und
    aktualisiert anschließend den lokalen Supervisor-App-Eintrag. GitHub wird
    dabei weder als Quelle noch als Transportweg verwendet.
#>
param(
    [string]$HomeAssistantHost = "192.168.178.77",
    [string]$SshUser = "homeassistant",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519_lumi"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path (Join-Path $repoRoot "kfz_schnaeppchen")).Path
$versionLine = Select-String -Path (Join-Path $source "config.yaml") -Pattern '^version:\s*"([^"]+)"$'
if (-not $versionLine) { throw "Version in config.yaml fehlt." }
$version = $versionLine.Matches[0].Groups[1].Value

if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw "SSH-Key nicht gefunden: $KeyPath"
}

$target = "/addons/kfz_schnaeppchen"
$remote = "$SshUser@$HomeAssistantHost"
$sshArgs = @(
    "-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
    "-o", "MACs=hmac-sha2-256", "-c", "aes256-gcm@openssh.com", $remote
)

# Windows PowerShell kann Binärdaten in einer Pipeline als Text umkodieren.
# Deshalb zuerst ein lokales TAR, danach klassische SCP-Übertragung (-O).
$tarball = Join-Path ([System.IO.Path]::GetTempPath()) "kfz_schnaeppchen_$version.tar"
$remoteTarball = "/tmp/kfz_schnaeppchen_$version.tar"
try {
    & tar --exclude="__pycache__" --exclude="*.pyc" --exclude=".git" -cf $tarball -C $source .
    if ($LASTEXITCODE -ne 0) { throw "Lokales TAR-Archiv konnte nicht erstellt werden." }

    $scpArgs = @(
        "-O", "-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "MACs=hmac-sha2-256", "-c", "aes256-gcm@openssh.com",
        $tarball, "${remote}:$remoteTarball"
    )
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { throw "Übertragung nach Home Assistant fehlgeschlagen." }

    & ssh @sshArgs "sudo test -d $target && sudo test -f $target/config.yaml"
    if ($LASTEXITCODE -ne 0) { throw "Remote-Ziel ist nicht vorhanden oder ungültig: $target" }

    & ssh @sshArgs "sudo tar -xf $remoteTarball -C $target; sudo rm -f $remoteTarball; sudo grep '^version:' $target/config.yaml"
    if ($LASTEXITCODE -ne 0) { throw "Remote-Archiv konnte nicht entpackt/verifiziert werden." }
}
finally {
    if (Test-Path -LiteralPath $tarball) { Remove-Item -LiteralPath $tarball -Force }
}

$supervisorCommand = @'
set -e
T=$(sudo cat /run/s6/container_environment/SUPERVISOR_TOKEN)
AUTH="Authorization: Bearer $T"
curl -fsS -X POST -H "$AUTH" http://supervisor/store/reload >/dev/null
sleep 3
if curl -fsS -X POST -H "$AUTH" http://supervisor/addons/local_kfz_schnaeppchen/update >/tmp/kfz_update.json 2>/tmp/kfz_update.err; then
    echo UPDATE_OK
    cat /tmp/kfz_update.json
else
    echo UPDATE_FAILED
    cat /tmp/kfz_update.err
    exit 1
fi
rm -f /tmp/kfz_update.json /tmp/kfz_update.err
'@

& ssh @sshArgs $supervisorCommand
if ($LASTEXITCODE -ne 0) { throw "Supervisor-Update fehlgeschlagen." }

Write-Host "KFZ Schnäppchen $version wurde lokal nach $target übertragen und aktualisiert."
