#!/usr/bin/env bash
set -euo pipefail

export TZ="Europe/Berlin"
export PYTHONUNBUFFERED=1
export DATA_DIR=/data
export PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

# Home Assistant mountet /data als root:root. Einmal beim Start korrigieren,
# danach läuft die App ohne Root-Rechte.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/tor /data/firefox_profile
    chown -R crawler:crawler /data
    chmod 700 /data/tor
    if [ "${KFZ_USE_TOR:-0}" = "1" ]; then
        echo "[KFZ Schnäppchen] Starte optionalen Tor-Dienst..."
        gosu crawler tor --RunAsDaemon 1 || true
    fi
    exec gosu crawler /run.sh
fi

echo "[KFZ Schnäppchen] Starte Weboberfläche + Crawler auf Port 8099..."
cd /app
exec python3 -m uvicorn kfz_crawler.web:app --host 0.0.0.0 --port 8099 \
    --proxy-headers --forwarded-allow-ips="*"
