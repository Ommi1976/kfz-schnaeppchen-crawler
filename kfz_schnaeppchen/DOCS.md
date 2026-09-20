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
| `use_browser` | Browser-Modus für Portale, die ihn unterstützen. AutoUncle nutzt den Browser unabhängig von dieser Option; mobile.de nutzt seine eigene Firefox-Session. |
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
| `exclude_makes` | Hersteller, die ausgeschlossen werden sollen (Komma-getrennt). |
| `exclude_models` | Modelle, die ausgeschlossen werden sollen (Komma-getrennt). |
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
- **mobile.de:** sucht über eine eigene, wiederverwendbare Firefox-Sitzung im
  Add-on. Akku (`bc`) und ACC (`spc`) werden als native Filter übergeben und
  auf der geladenen Seite überprüft. Für mindestens 450 km wird zunächst die
  verfügbare Stufe 400 km verwendet, anschließend lokal exakt nachgefiltert.
  Im toleranten Modus ergänzt eine Suche ohne Kapazitätsfilter die Treffer,
  damit fehlende Portal-kWh nicht automatisch zum Ausschluss führen.
- **Alle Portale:** zentraler **Nachfilter**, der ein Inserat nur ausschließt,
  wenn der Wert bekannt ist und ihn verletzt.

**Akku-Mindestkapazität:** Wird im Inseratstitel nach Angaben wie `62 kWh`
ausgewertet. Ein bekannter kleinerer Akku wird ausgeschlossen. Fehlt die
kWh-Angabe, darf ein Inserat als Fallback erscheinen, wenn die geforderte
elektrische Reichweite nachweislich erfüllt ist; so bleiben brauchbare Treffer
ohne Kapazitätsangabe erhalten.

**Ausstattung:** Unterstützte Merkmale werden an die jeweiligen Portale
übergeben, etwa Sitzheizung und ACC an mobile.de. Weitere Anforderungen lassen sich über **Stichwörter** ergänzen
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

### Autonome mobile.de-Suche

- HA bleibt der zentrale Datenspeicher. Profil: `/data/firefox_profile`;
  Suchfortschritt, Cache und Schutzpause: SQLite-Einstellungen. Kein dauerhaft
  laufender PC, Käuferkonto oder externer Cookie-Import erforderlich. Alte
  Cookie-Endpunkte bleiben kompatibel, beeinflussen die Sitzung aber nicht.
- Erst ein vollständiger Durchlauf bestätigt den Gesamtbestand. Pro Lauf
  werden maximal zwölf Suchseiten bearbeitet, danach wird automatisch
  fortgesetzt. Der Fortschritt wird erst nach dem Speichern der Treffer
  bestätigt; ein Wiederholungsfenster reduziert Lücken bei verschobenen Seiten.
  Innerhalb von 24 Stunden nach einem vollständigen Lauf werden jeweils die
  ersten zwei Seiten der Suchvarianten nach neuesten Inseraten geprüft.
- Sehr große Suchen werden innerhalb desselben Budgets in disjunkte
  Preisintervalle aufgeteilt, sofern eine Preisobergrenze vorliegt. Das ist
  keine Vollständigkeitsgarantie für ein laufend verändertes Portal.
- Ein gemeinsamer Browser-Worker serialisiert alle verwalteten Abrufe:
  mindestens zwölf Sekunden Abstand, höchstens 30 Suchseiten, zehn Details
  und 20 Zertifikatsbilder pro Stundenfenster, über alle Suchen zusammen.
  Normale Browser-Unterressourcen sind keine einzeln gezählten Seitenabrufe.
  Diese Werte sind lokale Arbeitsbudgets, keine vom Portal zugesicherten Limits.
- HTTP 403/429 oder eine Verifikationsseite pausieren Suche, Details und
  Bildabrufe gemeinsam: zwei, sechs, dann 24 Stunden; ein längeres
  `Retry-After` wird berücksichtigt (bis sieben Tage). Kein IP-/Cookie-Wechsel,
  kein CAPTCHA-Löser, keine sofortige Wiederholung. Auch Neustarts oder alte
  Cookie-Uploads löschen die Pause nicht. Danach wird automatisch erneut geprüft.
- Höchstens drei Detailseiten je Suchlauf, bevorzugt noch ungeprüfte Fahrzeuge
  mit fehlenden SoH-/Akku-/Reichweiten-/EZ-Angaben. Auch der gespeicherte
  Altbestand kommt an die Reihe. Details werden 24 Stunden, fehlgeschlagene
  Details sechs Stunden zwischengespeichert. Budgetpausen sind keine Negativbefunde.
- Relevant sind nur Fahrzeugdaten und Beschreibung, nicht Empfehlungen für
  andere Autos. SoH wird mit Belegstufe, Akku getrennt nach netto/brutto,
  Reichweite mit Messstandard und EZ getrennt vom Modelljahr ausgewertet.
  Dokumentartig bezeichnete Galeriebilder können im Hintergrund per OCR geprüft
  werden; fehlende Angaben werden nicht als bestätigt ausgegeben.
- Wiederholte Seiten, unbestätigte Leerstände, Parserlücken und Budgetenden
  gelten als Teilabruf, nicht als vollständiges Ergebnis. Bestands-Inserate
  werden deshalb bei einem solchen Lauf nicht als verschwunden markiert.
- Die Statuskarte **mobile.de-Sitzung** zeigt Automatik oder Schutzpause;
  der Hinweistext nennt den letzten Suchfortschritt. `/api/status` enthält
  zusätzlich `mobile_runtime` mit Budgetzählern und Pausenende.

### Grenzen der Quellen

- **AutoUncle:** wird als zusätzlicher Discovery-Kanal über eine persistente
  Firefox-Session abgefragt. Sichtbare Angebotskarten werden in URL, Preis, EZ,
  km, Leistung, Reichweite, Akkuangabe und Standort normalisiert. AutoUncle
  ist nicht als alleinige Quelle vorgesehen.
- **Bot-Schutz:** `mobile_de` kann trotz Firefox-Session durch Akamai/DataDome
  blockiert werden. Bestätigte Treffer bleiben dann erhalten und das Portal
  erhält eine Schutzpause. `autouncle` wird bei einem Block ebenfalls isoliert;
  die übrigen Portale laufen weiter. `heycar` zeigt unter hey.car inzwischen
  britische Inhalte und ist für DE nicht nutzbar.
- **Browser-Modus (`use_browser`, #1):** Das Add-on-Image enthält Playwright
  sowie Chromium und Firefox. Eine AutoUncle-/mobile.de-Anmeldung aus einem
  anderen Browser wird nicht automatisch übernommen. Das mobile.de-Profil
  verwaltet seine eigenen Sitzungscookies persistent im Add-on.
- **Konten und Suchagenten:** Ein mobile.de-Konto erlaubt gespeicherte Suchen
  und Benachrichtigungen, erweitert aber nicht automatisch den Datenzugriff des
  Crawlers. Der autonome Suchpfad benötigt kein Konto; Anmeldung ist keine
  API-Berechtigung und keine Garantie gegen Blocks.
- **Proxy (`proxy`):** optional fest konfigurierbar, kein automatischer Wechsel.
  Bei Änderung ist ein Neustart erforderlich. Auch ein Proxy garantiert keinen Zugriff.
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
