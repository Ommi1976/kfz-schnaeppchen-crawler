#!/usr/bin/env bash
set -euo pipefail

export TZ="Europe/Berlin"
export PYTHONUNBUFFERED=1
export DATA_DIR=/data
export PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

# Home Assistant mountet /data als root:root. Einmal beim Start korrigieren,
# danach läuft die App ohne Root-Rechte.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data /var/lib/tor /var/log/tor
    chown -R debian-tor:debian-tor /var/lib/tor /var/log/tor 2>/dev/null || true
    chmod 700 /var/lib/tor 2>/dev/null || true
    echo "[KFZ Schnäppchen] Starte integrierten Tor-Dienst..."
    tor --RunAsDaemon 1 || true
    mkdir -p /data
    chown -R crawler:crawler /data
    exec gosu crawler /run.sh
fi

echo "[KFZ Schnäppchen] Starte Weboberfläche + Crawler auf Port 8099..."
cd /app
exec python3 -m uvicorn kfz_crawler.web:app --host 0.0.0.0 --port 8099 \
    --proxy-headers --forwarded-allow-ips="*"
