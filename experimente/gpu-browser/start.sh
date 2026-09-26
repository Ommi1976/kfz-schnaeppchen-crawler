#!/usr/bin/env bash
# Modi: gpu | mobile <URL> | cookie
set -u
mkdir -p /out "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

{
    echo "== /dev/dri =="
    ls -l /dev/dri 2>&1
    for vendor in /sys/class/drm/renderD*/device/vendor; do
        echo "$vendor: $(cat "$vendor" 2>/dev/null)"
    done
} | tee /out/geraet.txt

modus="${1:-gpu}"
case "$modus" in
    gpu)
        exec cage -- python3 /app/messung.py 2>/out/cage.log ;;
    mobile)
        exec cage -- python3 /app/messung.py --mobile "${2:?URL fehlt}" 2>/out/cage.log ;;
    cookie)
        exec python3 /app/cookie_test.py /eingabe/edge-curl.txt ;;
    *)
        echo "Unbekannter Modus: $modus" >&2
        exit 2 ;;
esac
