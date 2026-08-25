# KFZ Schnäppchen – Home Assistant Add-on Repository 🚗

Findet Gebrauchtwagen-**Schnäppchen** über die größten deutschen Fahrzeug-
portale und meldet neue Treffer an Home Assistant – im Stil eines
„Schnäppchen Crawlers", aber für KFZ.

Für jede Suche werden alle aktiven Portale abgefragt, aus **allen** Treffern
ein **Marktpreis (Median)** berechnet und Inserate markiert, die deutlich
darunter liegen. Bereits gemeldete Fahrzeuge werden gemerkt – jedes Auto also
nur **einmal**.

## Portale
Stand aus echten Testläufen (2026-08):

| Portal | Ohne Browser | Mit Browser-Modus (#1) |
|---|---|---|
| **AutoScout24** | ✅ funktioniert (server-seitige Filter) | – |
| **Kleinanzeigen** | ✅ funktioniert (+ Detail-Anreicherung #4) | – |
| AutoUncle | ❌ HTTP 403 | ⚠️ lädt, aber nur wenige vorgerenderte Angebote |
| mobile.de | ⚠️ öffentliche Suche wird vorbereitet, häufig HTTP 403 (DataDome) | ❌ auch headless geblockt |
| heycar | ⚠️ 0 Treffer | ❌ liefert britische Inhalte (hey.car → UK) |

**Empfehlung:** AutoScout24 + Kleinanzeigen bleiben die verlässlichen Portale;
mobile.de ist zusätzlich wieder auswählbar und erhält einen direkten Suchlink.
Der Browser-Modus (Playwright) umgeht zwar die 403-Blocks
von AutoUncle, liefert dort aber nur eine dünne, vorgerenderte Liste; mobile.de
bleibt hart geblockt. Ein Proxy/Tor hilft gegen die Blocks **nicht** (Bot-
Fingerprinting, nicht IP). Ein geblocktes Portal stoppt die anderen **nicht**.

## So werden Schnäppchen erkannt
- **Erwarteter Preis statt Median (#3):** Aus allen Treffern wird per robuster
  Regression `ln(Preis) ~ Alter + km` der für *dieses* Fahrzeug erwartete Preis
  geschätzt (Ausreißer werden getrimmt; bei zu wenig Daten Fallback auf Median).
  Ein Inserat ist ein Schnäppchen, wenn es deutlich unter dem *erwarteten* Preis
  liegt – nicht bloß unter dem Durchschnitt.
- **Betrugs-/Ausreißerfilter (#5):** „Zu-gut-um-wahr"-Inserate (Export, Bastler,
  Motor-/Getriebeschaden, Unfall, ohne TÜV …) werden erkannt und **nicht**
  gemeldet, sondern nur gezählt. `unfallfrei` wird korrekt nicht geflaggt.
- **Detail-Anreicherung Kleinanzeigen (#4, optional):** Lädt Detailseiten nach
  und ermittelt Kraftstoff/Getriebe/Leistung strukturiert – so rutschen z. B.
  bei `fuel: diesel` keine Benziner mehr durch.
- **Filter:** Marke, Modell, Baujahr, Preis, km, Kraftstoff, Getriebe,
  Karosserie, Leistung (PS), Anbieter – plus Hersteller-/Modell-Ausschlüsse und
  E-Auto: Mindest-Reichweite und -Batteriekapazität.

## Als Home Assistant Add-on installieren

[![Add-Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FOmmi1976%2Fkfz-schnaeppchen-crawler)

**Ein-Klick:** Auf den Button oben klicken → HA-Adresse bestätigen → **Hinzufügen**.
Danach das Add-on im Store (Abschnitt „KFZ Schnäppchen Add-ons", ganz unten)
installieren.

**Manuell:**
1. **Einstellungen → Add-ons → Add-on Store**
2. Oben rechts **⋮ → Repositories**
3. URL hinzufügen: `https://github.com/Ommi1976/kfz-schnaeppchen-crawler`
4. Store neu laden, nach unten scrollen → **„KFZ Schnäppchen Crawler"** installieren

Details und alle Optionen: [kfz_schnaeppchen/DOCS.md](kfz_schnaeppchen/DOCS.md)

> Hinweis: Ein eigenes Add-on muss **einmalig** als Repository hinzugefügt
> werden – das ist bei Home Assistant für alle Nicht-Standard-Add-ons so.

## Ohne Home Assistant (Standalone-CLI)
Der Crawler läuft auch eigenständig auf jedem Rechner mit Python:
```bash
cd kfz_schnaeppchen
python -m pip install -r requirements.txt
cp crawler.example.yaml crawler.yaml  # crawler.yaml anpassen
python run.py                          # nur neue Schnäppchen
python run.py --all                    # alle aktuellen Schnäppchen
```

## Struktur
```
repository.yaml                 Add-on-Repository-Manifest (HA)
kfz_schnaeppchen/               das Add-on
  config.yaml                   Add-on-Manifest (Optionen + Schema)
  build.yaml  Dockerfile  run.sh  Docker-Build & Start
  DOCS.md                       Add-on-Dokumentation (HA-Tab „Documentation")
  requirements.txt  run.py      Standalone-Betrieb
  crawler.example.yaml          Beispielkonfig für Standalone
  kfz_crawler/                  die eigentliche Anwendung
    main.py        CLI-Runner
    ha_run.py      Add-on-Runner (liest /data/options.json, Intervall-Schleife)
    config.py      Konfiguration
    models.py      Listing / SearchQuery
    dealfinder.py  Marktpreis (Median) + Rabatt
    storage.py     SQLite (gesehene Inserate)
    notify.py      Konsole / Telegram / Home Assistant
    portals/       autoscout24 · kleinanzeigen · autouncle · mobile_de · heycar
```

## Hinweise
- **Selektoren:** Portale ändern regelmäßig ihr HTML/JSON. Liefert ein Portal
  dauerhaft 0 Treffer, müssen die Parser in `kfz_crawler/portals/` angepasst werden.
- **Fairer Umgang:** `request_delay` nicht zu klein setzen und die Nutzungs-
  bedingungen der Portale beachten. Für den privaten Gebrauch gedacht.
