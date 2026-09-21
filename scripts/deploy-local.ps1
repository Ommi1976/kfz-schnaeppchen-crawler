<#
.SYNOPSIS
    Deployt den committeten Release direkt als lokale HAOS-App.
.DESCRIPTION
    Version und Quellstand bleiben unverändert. Keine Browserprofile im Paket.
    Synchronisiert /addons und den tatsächlichen Supervisor-Buildkontext.
    Vorheriger Quellstand und Transportarchiv bleiben unter /tmp erhalten.
#>
param(
    [string]$HomeAssistantHost = "192.168.178.77",
    [string]$SshUser = "homeassistant",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519_lumi"
)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configContent = Get-Content -Raw -LiteralPath (Join-Path $repoRoot "kfz_schnaeppchen/config.yaml")
if ($configContent -notmatch 'version:\s*"(\d+\.\d+\.\d+)"') { throw "Ungültige Release-Version" }
$version = $matches[1]
$pending = & git -C $repoRoot status --porcelain -- kfz_schnaeppchen
if ($LASTEXITCODE -ne 0 -or $pending) { throw "Add-on-Änderungen vor Deployment committen." }
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw "SSH-Schlüssel fehlt" }
$tarball = Join-Path ([IO.Path]::GetTempPath()) "kfz-release-$version.tar"
& git -C $repoRoot archive --format=tar -o $tarball HEAD kfz_schnaeppchen
if ($LASTEXITCODE -ne 0) { throw "Release-Archiv fehlgeschlagen" }
$remote = "$SshUser@$HomeAssistantHost"
$connection = @("-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
    "-o", "MACs=hmac-sha2-256", "-c", "aes256-gcm@openssh.com")
& scp -O @connection $tarball ("{0}:/tmp/kfz-release-{1}.tar" -f $remote, $version)
if ($LASTEXITCODE -ne 0) { throw "Übertragung fehlgeschlagen" }

$remoteScript = @'
set -eu
test -d /addons/kfz_schnaeppchen
docker exec hassio_supervisor test -d /data/apps/local/kfz_schnaeppchen
tar -cf /tmp/kfz-before-__VERSION__.tar -C /addons kfz_schnaeppchen
docker exec hassio_supervisor tar -cf /tmp/kfz-before-__VERSION__.tar -C /data/apps/local kfz_schnaeppchen
tar -xf /tmp/kfz-release-__VERSION__.tar -C /addons
docker cp /tmp/kfz-release-__VERSION__.tar hassio_supervisor:/tmp/kfz-release-__VERSION__.tar
docker exec hassio_supervisor tar -xf /tmp/kfz-release-__VERSION__.tar -C /data/apps/local
docker exec -i hassio_supervisor python3 - <<'PY'
import json, urllib.request
token = json.load(open('/data/cli.json'))['access_token']
def call(path, method='GET'):
    request = urllib.request.Request('http://supervisor'+path, method=method,
        headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request, timeout=600) as response:
        data=json.load(response)
    if data.get('result') != 'ok':
        raise RuntimeError('Supervisor-Aktion fehlgeschlagen: '+path)
    return data.get('data', {})
call('/store/reload','POST')
print('Lokaler Store neu geladen; Build startet.', flush=True)
call('/addons/local_kfz_schnaeppchen/rebuild','POST')
info=call('/addons/local_kfz_schnaeppchen/info')
if info.get('state') != 'started':
    call('/addons/local_kfz_schnaeppchen/start','POST')
print('Build und Start abgeschlossen.', flush=True)
PY
for n in $(seq 1 30); do
  if curl -fsS --max-time 5 http://127.0.0.1:8099/api/status | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["version"]=="__VERSION__"; print("Live-Version:",d["version"])'; then
    docker ps --filter name=app_local_kfz_schnaeppchen --format '{{.Names}} {{.Image}} {{.Status}}'
    exit 0
  fi
  sleep 2
done
echo 'Live-Versionsprüfung fehlgeschlagen' >&2
exit 1
'@
$remoteScript = $remoteScript.Replace("__VERSION__", $version)
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript.Replace([string][char]13, "")))
& ssh @connection $remote "echo $encoded | base64 -d | sudo bash"
if ($LASTEXITCODE -ne 0) { throw "Deployment oder Live-Prüfung fehlgeschlagen" }
Write-Host "KFZ Schnäppchen $version läuft aus dem lokalen HAOS-Build."
