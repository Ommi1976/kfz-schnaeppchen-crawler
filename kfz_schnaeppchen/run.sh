#!/usr/bin/env bash
set -euo pipefail

export TZ="Europe/Berlin"
export PYTHONUNBUFFERED=1
export DATA_DIR=/data

# Home Assistant mountet /data als root:root. Einmal beim Start korrigieren,
# danach läuft die App ohne Root-Rechte.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R crawler:crawler /data
    exec gosu crawler /run.sh
fi

echo "[KFZ Schnäppchen] Starte Crawler (Intervall-Schleife)..."
cd /app
exec python3 -m kfz_crawler.ha_run
