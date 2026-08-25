# Konzept: AutoScout24-Suchfilter im KFZ Schnäppchen Crawler

**Stand:** 2026-08 · **Autor:** analysiert aus AutoScout24 `__NEXT_DATA__` (taxonomy) + empirischer Parameter-Verifikation

Ziel: Die Weboberfläche soll dieselben Suchoptionen bieten wie AutoScout24 selbst
(Marke, Modell, Kraftstoff, **Ausstattung** usw.). Dieses Dokument inventarisiert
die real verfügbaren Filter, verifiziert die URL-Parameter und schlägt eine
phasenweise Umsetzung vor.

---

## 1. Vorgehen & Datenquelle

AutoScout24 rendert auf jeder Ergebnisseite ein `__NEXT_DATA__`-JSON mit einem
vollständigen `taxonomy`-Objekt (alle Auswahllisten inkl. interner IDs). Die
Parameternamen wurden **empirisch verifiziert**, indem Kandidaten-Parameter
angewandt und die `numberOfResults` verglichen wurden (Basis: VW Golf Diesel =
7.970 Treffer).

Damit sind Optionen **und** Parameter belegt – keine Rätselei nötig.

---

## 2. Filter-Inventar (AutoScout24)

Legende: ✅ = Parameter empirisch bestätigt · ⭑ = Wert-Semantik noch zu prüfen ·
🟢 = bereits im Crawler umgesetzt

| Filter | URL-Parameter | Werte / Kodierung | Status |
|---|---|---|---|
| Marke / Modell | Pfad `/lst/<marke>/<modell>` bzw. `mmmv` | Slugs bzw. IDs (`taxonomy.makes`/`models`) | 🟢 |
| Preis | `pricefrom` / `priceto` | € | 🟢 |
| Erstzulassung | `fregfrom` / `fregto` | Jahr | 🟢 |
| Kilometer | `kmfrom` / `kmto` | km | 🟢 |
| Leistung | `powerfrom` / `powerto` + `powertype=kw` | kW (aus PS umgerechnet) | 🟢 |
| Kraftstoff | `fuel` | B Benzin · D Diesel · E Elektro · C CNG · L LPG · H H₂ · M Ethanol · O Sonstige · 2 El/Benzin · 3 El/Diesel | 🟢 |
| Getriebe | `gear` | M Schaltung · A Automatik · S Halbautomatik | 🟢 ✅ |
| **Karosserie** | **`body`** | 1 Kleinwagen · 2 Cabrio · 3 Coupé · 4 SUV/Gelände · 5 Kombi · 6 Limousine · 12 Van/Kleinbus · 13 Transporter · 7 Sonstige | ✅ |
| **Türen** | **`doorfrom` / `doorto`** | 2–3 / 4–5 | ✅ |
| **Ausstattung** | **`eq`** | Komma-Liste von IDs (136 Merkmale, siehe §3) | ✅ |
| E-Reichweite | `erange` | km (nur Elektro) | ✅ 🟢(Client) |
| Anbieter | `customertype` | D Händler · P Privat | 🟢 ⭑ |
| Antrieb | `drivetrain` | Front/Heck/Allrad | ⭑ |
| Schadstoffklasse | `emclass`* | 1–6, 11 (6b), 7 (6c), 8 (6d), 9 (6d-TEMP), 10 (6e) | ⭑ |
| Umweltplakette | `eco` | 1–4 (4 = grün) | ⭑ |
| Vorbesitzer | `prevowner` | max. Anzahl | ⭑ |
| Preisbewertung | `priceevaluation` | AS24-Rating (z. B. „guter Preis") | ⭑ |
| Zylinder | `cy` | Anzahl | ⭑ |
| Sitze | `seatfrom` / `seatto` | Anzahl | ⭑ |
| Farbe außen | `bcol` | `taxonomy.bodyColor` | ⭑ |
| Innenfarbe / Polster | `icol` / `upholstery` | Taxonomie | ⭑ |
| Akku Kauf/Miete | `batteryOwnershipType` | Kauf / Miete | ⭑ |
| Online seit | `onlinesince` | Tage | ⭑ |
| Unfallfrei / Zustand | `damaged_listing`, `conditionEquipment` | Flags | ⭑ |

\* exakter Parametername (`emclass` vs. `emissionclass`) bei der Umsetzung
final gegen die Seite prüfen.

---

## 3. Ausstattung (`eq`) – der Kernwunsch

AutoScout24 kennt **136 Ausstattungsmerkmale**, gefiltert über `eq=<id>,<id>,…`.
Server-seitig verifiziert (z. B. `eq=34` „Sitzheizung": 7.970 → 6.729 Treffer).

Für die UI empfiehlt sich ein **gruppierter Mehrfach-Auswahl-Picker**. Vorschlag
für Gruppen und die wichtigsten Merkmale (ID in Klammern):

- **Komfort/Klima:** Klimaanlage (5), Klimaautomatik (30), 2-/3-/4-Zonen (241/242/243), Sitzheizung (34), Sitzheizung hinten (248), Sitzbelüftung (154), Standheizung (52), Beheizbares Lenkrad (136), Massagesitze (145), El. Sitze (16), Panoramadach (50), Schiebedach (4), Anhängerkupplung (20)
- **Assistenz/Sicherheit:** Abstandstempomat/ACC (133), Tempomat (38), Spurhalteassistent (157), Totwinkel-Assistent (158), Notbremsassistent (148), Verkehrszeichenerkennung (162), Müdigkeitswarner (146), Head-up-Display (123), 360°-Kamera (187), Einparkhilfe (40) / Rückfahrkamera (130), Isofix (125)
- **Multimedia/Konnektivität:** Navigationssystem (23), Apple CarPlay (221), Android Auto (222), Bluetooth (122), DAB-Radio (138), Touchscreen (159), Soundsystem (155), Volldigitales Kombiinstrument (224), Wireless Charging (223), WLAN-Hotspot (220)
- **Licht:** LED-Scheinwerfer (140), Voll-LED (239), Xenon (39), Bi-Xenon (230), Matrix/Blendfrei (214), Kurvenlicht (118), LED-Tagfahrlicht (141)
- **Antrieb/Fahrwerk:** Allrad (11), Sportfahrwerk (116), Luftfederung (144), Start/Stop (113), Schaltwippen (151)
- **Räder/Außen:** Alufelgen (15), Winterreifen (25), Allwetterreifen (211), Dachreling (27), Metallic (via Farbe)
- **E-Auto:** Wärmepumpe (249), Bidirektionales Laden (250), Batteriezertifikat (251), Reichweitenverlängerer (237)

Die vollständige ID-Liste wird als Konstante `AS24_EQUIPMENT` im Code hinterlegt
(alle 136 Paare liegen bereits extrahiert vor).

---

## 4. Portalübergreifende Strategie

Nur AutoScout24 bietet alle Filter server-seitig. Grundprinzip bleibt:

- **AutoScout24:** so viel wie möglich **server-seitig** (URL-Parameter) → weniger
  Traffic, exakte Ergebnisse. Inkl. `body`, `gear`, `doorfrom/to`, `eq`, `erange`.
- **Kleinanzeigen / (AutoUncle/mobile.de via Browser):** kein Ausstattungsfilter
  in der Liste → **Nachfilter** über `keywords`/`exclude_terms` (bereits vorhanden)
  und, wo `verify_details` aktiv ist, über die strukturierten Detailfelder.
- Merkmale ohne Server-Support werden generell über den zentralen
  `matches_query`-Nachfilter angewandt (nur ausschließen, wenn Wert bekannt).

**Ausstattung cross-portal:** AS24 per `eq`, sonst per Stichwort-Mapping
(z. B. Merkmal „Navigationssystem" → Stichwörter „navi, navigation").

---

## 5. Erweiterung des Datenmodells (`SearchQuery`)

Vorhanden: make, model, year_from/to, price_from/to, mileage_from/to, fuel,
transmission, body_type, power_from/to, seller, doors, ev_range_from,
battery_from_kwh, keywords, exclude_terms.

**Neu vorgeschlagen:**

| Feld | Typ | Mapping AS24 |
|---|---|---|
| `equipment` | Liste[int] | `eq` (IDs) |
| `drivetrain` | str (front/heck/allrad) | `drivetrain` |
| `emission_class` | str (euro4/5/6/6d) | `emclass` |
| `emission_sticker` | int (Plakette 1–4) | `eco` |
| `previous_owners_max` | int | `prevowner` |
| `price_evaluation` | str (AS24-Rating) | `priceevaluation` |
| `body_color` | str | `bcol` |
| `seats_from`/`seats_to` | int | `seatfrom`/`seatto` |
| `online_since_days` | int | `onlinesince` |
| `damaged` | bool (unfallfrei) | `damaged_listing=0` |

`body_type` intern zusätzlich auf die AS24-`body`-IDs abbilden (Mapping-Tabelle).

---

## 6. UI-Konzept

Erweiterung des bestehenden Suchformulars:

1. **Grunddaten** (wie jetzt): Name, Marke, Modell, Preis, Baujahr, km, aktiv.
2. **Fahrzeug**: Kraftstoff, Getriebe, Karosserie, Türen, Sitze, Antrieb, Leistung.
3. **Ausstattung** (neu): aufklappbarer, nach Gruppen sortierter **Checkbox-Picker**
   (Komfort/Assistenz/Multimedia/Licht/…), mit Suchfeld zum schnellen Finden.
4. **Zustand & Umwelt**: Schadstoffklasse, Umweltplakette, Vorbesitzer, unfallfrei.
5. **E-Auto** (kontextabhängig bei `fuel=elektro`): Reichweite, Akku-Kapazität,
   Batterie Kauf/Miete.
6. **Anbieter & Preisbewertung**: Händler/Privat, AS24-Preisbewertung.
7. **Freitext**: Stichwörter / Ausschluss (wie jetzt) – wirkt cross-portal.

Marke/Modell perspektivisch als **Dropdown aus der AS24-Taxonomie** (mit IDs)
statt Freitext – erhöht Trefferqualität und ermöglicht Modellvarianten.

---

## 7. Umsetzung in Phasen

**Phase 1 – Quick Wins (server-seitig, geringes Risiko)**
- `body` (Karosserie-IDs) statt nur Titel-Keyword
- `doorfrom/doorto` (Türen), `gear` (schon da), `customertype` (schon da)
- **`eq` (Ausstattung)** mit gruppiertem Checkbox-Picker + `AS24_EQUIPMENT`-Konstante
- `erange` server-seitig auch in die URL (bisher nur Client-Nachfilter)

**Phase 2 – Zustand & Umwelt**
- `emclass`, `eco`, `prevowner`, `damaged_listing`, `priceevaluation`
- Wert-Semantik der ⭑-Parameter vorab an der Live-Seite verifizieren

**Phase 3 – Komfort & Cross-Portal-Feinschliff**
- Marke/Modell-Dropdown aus Taxonomie (inkl. Modellvarianten)
- Ausstattung→Stichwort-Mapping für Kleinanzeigen etc.
- Farben, Sitze, „online seit", Akku Kauf/Miete

---

## 8. Offene Punkte / zu verifizieren

- Exakte Wert-Semantik von `drivetrain`, `emclass` vs. `emissionclass`, `eco`,
  `prevowner`, `priceevaluation` (Parametername akzeptiert, Werte prüfen).
- Multi-Marken/Modell-Kodierung `mmmv` (für „mehrere Modelle je Suche").
- Ausstattungs-Mapping für Nicht-AS24-Portale (Pflege der Stichwort-Synonyme).
- Taxonomie ändert sich selten, aber IDs sollten bei Bruch nachgezogen werden
  (Quelle: `__NEXT_DATA__.props.pageProps.taxonomy`).
