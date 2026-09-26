#!/usr/bin/env bash
set -euo pipefail
umask 077

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
    echo "[KFZ Schnäppchen] Starte virtuellen Browser-Bildschirm..."
    gosu crawler Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/kfz-xvfb.log 2>&1 &
    export DISPLAY=:99

    # mobile.de: Chrome auf der echten GPU. Xvfb rendert prinzipbedingt in
    # Software (SwiftShader) – dieses Merkmal hat Akamai gesehen. cage rendert
    # ohne Bildschirm über das Render-Device. Fehlt es, bleibt mobile.de bei
    # Firefox auf Xvfb.
    gpu_option=$(python3 -c 'import json; o = json.load(open("/data/options.json")); print(0 if o.get("mobile_gpu_browser") is False else 1)' 2>/dev/null || echo 1)
    if [ "$gpu_option" = "1" ] && [ -e /dev/dri/renderD128 ] && [ -x /opt/google/chrome/chrome ]; then
        echo "[KFZ Schnäppchen] Starte GPU-Anzeige für mobile.de..."
        runtime=/tmp/kfz-wayland
        mkdir -p "$runtime"
        chown crawler:crawler "$runtime"
        chmod 700 "$runtime"
        gosu crawler env XDG_RUNTIME_DIR="$runtime" WLR_BACKENDS=headless \
            WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 \
            WLR_LIBINPUT_NO_DEVICES=1 cage -- sleep infinity >/tmp/kfz-cage.log 2>&1 &
        for _ in $(seq 1 20); do
            ls "$runtime"/wayland-[0-9] >/dev/null 2>&1 && break
            sleep 0.5
        done
        if ls "$runtime"/wayland-[0-9] >/dev/null 2>&1; then
            export KFZ_WAYLAND_RUNTIME="$runtime"
        else
            echo "[KFZ Schnäppchen] GPU-Anzeige nicht gestartet; mobile.de nutzt Firefox." >&2
            tail -n 5 /tmp/kfz-cage.log >&2 || true
        fi
    fi
    exec gosu crawler /run.sh
fi

echo "[KFZ Schnäppchen] Starte Weboberfläche + Crawler auf Port 8099..."
cd /app
exec python3 -m uvicorn kfz_crawler.web:app --host 0.0.0.0 --port 8099 \
    --no-proxy-headers --no-access-log
