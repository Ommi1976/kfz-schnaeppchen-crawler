# KFZ Schnäppchen Crawler – Add-on-Dokumentation

Findet Gebrauchtwagen-**Schnäppchen** über die größten deutschen Fahrzeug-
portale und meldet neue Treffer an Home Assistant.

Für jede Suche werden alle aktiven Portale abgefragt, aus **allen** Treffern
ein **Marktpreis (Median)** berechnet und Inserate markiert, die deutlich
darunter liegen. Bereits gemeldete Fahrzeuge werden in `/data/seen.sqlite`
gemerkt – so bekommst du jedes Auto nur **einmal** gemeldet.

## Installation
1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store**.
2. Oben rechts über das Drei-Punkte-Menü **Repositories** öffnen.
3. Die URL dieses Repositories hinzufügen.
4. Das Add-on **„KFZ Schnäppchen Crawler"** installieren und starten.

## Konfiguration

| Option | Bedeutung |
|---|---|
| `interval_minutes` | Wie oft gesucht wird (Minuten). |
| `deal_threshold` | Ab wie viel **Prozent** unter dem Marktpreis ein Inserat als Schnäppchen gilt (z. B. `15`). |
| `min_comparables` | Mindestzahl vergleichbarer Inserate, damit der Marktpreis als verlässlich gilt. |
| `request_delay` | Wartezeit zwischen Anfragen (Sekunden) – Portale schonen. |
| `max_pages` | Ergebnisseiten pro Portal & Suche. |
| `suspicious_discount` | Ab wie viel **%** unter erwartetem Preis ein Inserat als verdächtig gilt und **nicht** gemeldet wird (z. B. `60`). |
| `verify_details` | Kleinanzeigen-Detailseiten nachladen für exakte Kraftstoff/Getriebe/Leistung (genauer, mehr Requests). |
| `use_browser` | Playwright-Browser für geblockte Portale nutzen. Umgeht AutoUncle-403 (dort aber dünne Liste), **nicht** mobile.de. Im Standard-Add-on-Image nicht enthalten. |
| `portals` | Aktive Portale: `autoscout24`, `kleinanzeigen`, `autouncle`, `mobile_de`, `heycar`. |
| `searches` | Deine Suchen (siehe unten). |
| `notify_persistent` | Persistente HA-Benachrichtigung bei neuen Schnäppchen. |
| `notify_service` | Optionaler Notify-Dienst, z. B. `notify.mobile_app_dein_handy`. |
| `telegram_enabled` / `telegram_bot_token` / `telegram_chat_id` | Optionale Telegram-Benachrichtigung. |

### Verfügbare Filter je Suche
Alle außer `name` sind optional.

| Filter | Werte / Einheit |
|---|---|
| `make`, `model` | z. B. `volkswagen`, `golf` |
| `year_from`, `year_to` | Erstzulassung (Jahr) |
| `price_from`, `price_to` | Preis in € |
| `mileage_from`, `mileage_to` | Kilometerstand |
| `fuel` | `benzin` `diesel` `elektro` `hybrid` `lpg` `cng` |
| `transmission` | `schaltgetriebe` `automatik` |
| `body_type` | `limousine` `kombi` `suv` `cabrio` `coupe` `van` `kleinwagen` |
| `power_from`, `power_to` | Leistung in **PS** |
| `seller` | `haendler` `privat` |
| `doors` | `2/3` `4/5` |
| **`ev_range_from`** | E-Auto: Mindest-Reichweite (km) |
| **`battery_from_kwh`** | E-Auto: Mindest-Batteriekapazität (kWh) |

### Beispiele
```yaml
searches:
  - name: VW Golf Diesel Automatik ab 110 PS
    make: volkswagen
    model: golf
    year_from: 2015
    price_to: 15000
    mileage_to: 150000
    fuel: diesel
    transmission: automatik
    power_from: 110
    seller: haendler
  - name: E-Auto bis 25k mit Reichweite
    fuel: elektro
    price_to: 25000
    year_from: 2019
    ev_range_from: 300
    battery_from_kwh: 40
```

**So wirken die Filter (portalübergreifend homogenisiert):**
Der **gemeinsame Filtersatz** (Marke, Modell, Preis, Baujahr, km, Kraftstoff,
Getriebe, Leistung, Karosserie, Anbieter) steuert **alle Portale einheitlich**:

- **AutoScout24:** wendet alles server-seitig an (inkl. Karosserie, Türen,
  E‑Reichweite und – als einziges Portal – die 136 Ausstattungsmerkmale via `eq`).
- **Kleinanzeigen:** die Trefferliste liefert Kraftstoff/Getriebe/Leistung nicht;
  daher werden bei Bedarf **automatisch die Detailseiten nachgeladen**, damit
  derselbe Filter greift (z. B. `fuel: diesel` behält wirklich nur Diesel).
- **Alle Portale:** zentraler **Nachfilter**, der ein Inserat nur ausschließt,
  wenn der Wert bekannt ist und ihn verletzt.

**Akku-Mindestkapazität:** Wird im Inseratstitel nach Angaben wie `62 kWh`
ausgewertet. Ein bekannter kleinerer Akku wird ausgeschlossen. Fehlt die
kWh-Angabe, darf ein Inserat als Fallback erscheinen, wenn die geforderte
elektrische Reichweite nachweislich erfüllt ist; so bleiben brauchbare Treffer
ohne Kapazitätsangabe erhalten.

**Ausstattung:** Die Ausstattungs-Auswahl in der UI wirkt server-seitig nur bei
AutoScout24. Für andere Portale dieselbe Wirkung über **Stichwörter** erzielen
(z. B. „navi, ahk"). Tipp: Bei `fuel: elektro` ohne `make`/`model` mit
`power_from` (echte PKW) oder Marke/Modell eingrenzen.

## Benachrichtigungen in Home Assistant
- **Persistent:** Erscheint als Benachrichtigung in der HA-Oberfläche.
- **Notify-Dienst:** Setze `notify_service` auf einen vorhandenen Dienst
  (z. B. Companion-App), um Push aufs Handy zu bekommen.

Beispiel-Automation als Reaktion auf eine persistente Benachrichtigung ist
nicht nötig – das Add-on ruft die Dienste direkt auf.

## Preismodell & Betrugsfilter
- **Erwarteter Preis (#3):** Aus allen Treffern wird per Regression
  `ln(Preis) ~ Alter + km` der erwartete Preis je Fahrzeug geschätzt; Ausreißer
  werden getrimmt. Bei zu wenigen/varianzarmen Daten Fallback auf den Median.
  Der `deal_threshold` bezieht sich auf diesen **erwarteten** Preis.
- **Verdachtsfilter (#5):** Inserate mit Export/Bastler/Motorschaden/Unfall …
  oder mit Rabatt ≥ `suspicious_discount` werden unterdrückt (im Log gezählt).

## Hinweise & Grenzen
- **Bot-Schutz:** `mobile_de` liefert **HTTP 403** (DataDome – auch mit Browser).
  `autouncle` ist ohne Browser 403; mit `use_browser` erreichbar, liefert aber
  nur wenige vorgerenderte Angebote. `heycar` zeigt unter hey.car inzwischen
  britische Inhalte und ist für DE nicht nutzbar. Verlässlich sind
  **`autoscout24`** und **`kleinanzeigen`** (Standard). Ein blockiertes Portal
  stoppt die anderen **nicht**.
- **Browser-Modus (`use_browser`, #1):** benötigt Playwright + Chromium. Im
  schlanken Standard-Add-on-Image nicht enthalten; primär für den
  Standalone-Betrieb (`pip install -r requirements-browser.txt && playwright install chromium`).
- **Proxy/Tor (`proxy`):** hilft nur gegen IP-Rate-Limits, **nicht** gegen die
  403-Blocks. Für Tor z. B. `socks5h://127.0.0.1:9050`.
- **Selektor-Änderungen:** Portale ändern regelmäßig ihr HTML/JSON. Liefert
  ein Portal dauerhaft 0 Treffer, müssen die Parser im Code angepasst werden.
- **Fairer Umgang:** `request_delay` nicht zu klein wählen und die
  Nutzungsbedingungen der Portale beachten. Für den privaten Gebrauch gedacht.

## Fehlersuche
- Log ansehen: Add-on-Seite → Tab **Log**.
- „Keine neuen Schnäppchen": normal, wenn nichts unter dem Marktpreis liegt
  oder alles schon gemeldet wurde.
- Marktpreis wird nur berechnet, wenn mindestens `min_comparables` Inserate
  mit Preis gefunden werden.
