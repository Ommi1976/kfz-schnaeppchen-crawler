<#
.SYNOPSIS
    Baut den Messcontainer auf dem HA-Server und führt eine Messstufe aus.
.DESCRIPTION
    Das Add-on bleibt unberührt. Der Container läuft nur für die Dauer der
    Messung und wird danach entfernt; das Ergebnis landet unter ergebnisse\.
      gpu    - nur lokale Seiten, kein Kontakt zu mobile.de
      mobile - Stufe gpu, danach genau EIN Aufruf von -MobileUrl
      cookie - EINE Anfrage per curl_cffi mit der Edge-Sitzung aus -CurlDatei
.EXAMPLE
    .\run-messung.ps1 -Stufe gpu
    .\run-messung.ps1 -Stufe cookie -CurlDatei .\edge-curl.txt
#>
param(
    [ValidateSet("gpu", "mobile", "cookie")]
    [string]$Stufe = "gpu",
    [string]$MobileUrl = "https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&s=Car&vc=Car&pageNumber=1&sb=doc&od=down&p=5000%3A15000&fr=2015%3A2020",
    [string]$CurlDatei = "",
    [switch]$ImageEntfernen,
    [string]$HomeAssistantHost = "192.168.178.77",
    [string]$SshUser = "homeassistant",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519_lumi"
)
$ErrorActionPreference = "Stop"
$ordner = $PSScriptRoot
if ($MobileUrl -match "'") { throw "URL darf kein Hochkomma enthalten" }
if ($Stufe -eq "cookie" -and -not (Test-Path -LiteralPath $CurlDatei -PathType Leaf)) {
    throw "Für -Stufe cookie wird -CurlDatei benötigt (Edge-DevTools: Als cURL (bash) kopieren)"
}

$paket = Join-Path ([IO.Path]::GetTempPath()) "kfz-gpu-paket.tar"
& tar -cf $paket -C $ordner Dockerfile start.sh messung.py cookie_test.py
if ($LASTEXITCODE -ne 0) { throw "Paket fehlgeschlagen" }

$remote = "$SshUser@$HomeAssistantHost"
$connection = @("-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
    "-o", "MACs=hmac-sha2-256", "-c", "aes256-gcm@openssh.com")
& ssh @connection $remote "mkdir -p /tmp/kfz-gpu-messung"
& scp -O @connection $paket "${remote}:/tmp/kfz-gpu-messung/paket.tar"
if ($LASTEXITCODE -ne 0) { throw "Übertragung fehlgeschlagen" }
if ($Stufe -eq "cookie") {
    & scp -O @connection $CurlDatei "${remote}:/tmp/kfz-gpu-messung/edge-curl.txt"
    if ($LASTEXITCODE -ne 0) { throw "Übertragung der cURL-Datei fehlgeschlagen" }
}

$argumente = switch ($Stufe) {
    "gpu"    { "gpu" }
    "mobile" { "mobile '$MobileUrl'" }
    "cookie" { "cookie" }
}
$aufraeumen = if ($ImageEntfernen) { "docker rmi kfz-gpu-messung >/dev/null || true" } else { "" }

$remoteScript = @'
set -eu
W=/tmp/kfz-gpu-messung
rm -rf "$W/ctx" "$W/out" "$W/out.tar"
mkdir -p "$W/ctx"
tar -xf "$W/paket.tar" -C "$W/ctx"
echo "Baue Messcontainer (beim ersten Mal einige Minuten)..."
if ! docker build -t kfz-gpu-messung "$W/ctx" >"$W/build.log" 2>&1; then
    echo "Build fehlgeschlagen - Ende von build.log:"
    tail -n 40 "$W/build.log"
    exit 1
fi
docker rm -f kfz-gpu-lauf >/dev/null 2>&1 || true
docker create --name kfz-gpu-lauf --device /dev/dri/renderD128 --shm-size=1g \
    kfz-gpu-messung __ARGUMENTE__ >/dev/null
if [ -f "$W/edge-curl.txt" ]; then
    chmod 644 "$W/edge-curl.txt"
    docker cp "$W/edge-curl.txt" kfz-gpu-lauf:/eingabe/edge-curl.txt
fi
rc=0
docker start -a kfz-gpu-lauf || rc=$?
docker cp kfz-gpu-lauf:/out "$W/out" || true
docker rm -f kfz-gpu-lauf >/dev/null
rm -f "$W/edge-curl.txt"
tar -cf "$W/out.tar" -C "$W" out
chmod 644 "$W/out.tar"
__AUFRAEUMEN__
exit $rc
'@
$remoteScript = $remoteScript.Replace("__ARGUMENTE__", $argumente).Replace("__AUFRAEUMEN__", $aufraeumen)
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript.Replace([string][char]13, "")))
# stderr von ssh darf die Messung nicht abbrechen; maßgeblich ist der Exit-Code.
$ErrorActionPreference = "Continue"
& ssh @connection $remote "echo $encoded | base64 -d | sudo bash 2>&1"
$rc = $LASTEXITCODE

$ziel = Join-Path $ordner ("ergebnisse\" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-$Stufe")
New-Item -ItemType Directory -Force $ziel | Out-Null
& scp -O @connection "${remote}:/tmp/kfz-gpu-messung/out.tar" (Join-Path $ziel "out.tar")
if ($LASTEXITCODE -eq 0) {
    & tar -xf (Join-Path $ziel "out.tar") -C $ziel
    Remove-Item (Join-Path $ziel "out.tar")
    $log = Join-Path $ziel "out\cage.log"
    if (-not (Test-Path (Join-Path $ziel "out\ergebnis.json")) -and (Test-Path $log)) {
        Write-Host "Kein Ergebnis - Ende von cage.log:"
        Get-Content $log -Tail 30
    }
}
Write-Host "Ergebnisse: $ziel (Exit-Code $rc)"
