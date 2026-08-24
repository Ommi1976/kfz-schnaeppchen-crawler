#!/usr/bin/with-contenv bashio
# Startet den KFZ Schnäppchen Crawler im Add-on-Kontext.
# Die Konfiguration wird von Home Assistant nach /data/options.json geschrieben
# und direkt von kfz_crawler.ha_run gelesen.

bashio::log.info "Starte KFZ Schnäppchen Crawler…"

cd /app || bashio::exit.nok "Arbeitsverzeichnis /app fehlt"

exec python3 -m kfz_crawler.ha_run
