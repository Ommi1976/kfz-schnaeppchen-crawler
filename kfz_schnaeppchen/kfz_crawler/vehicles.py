"""Fahrzeugidentitaet und Angebotszuordnung (K4 §4 und §7).

Ein reales Fahrzeug kann gleichzeitig auf mehreren Portalen angeboten werden;
gemessen ueberschneiden sich die Kataloge zu rund 18 %. Ohne Trennung von
Fahrzeug und Angebot erscheint dasselbe Auto mehrfach, und die Links der
Zweitangebote gehen verloren.

Baujahr und Kilometerstand allein reichen zur Identifikation NICHT aus: zwei
Fahrzeuge derselben Baureihe koennen identische Werte haben. Deshalb wird
bewertet statt verglichen, und nur ausreichend sichere Zuordnungen werden
zusammengefuehrt. Jede Zuordnung wird mit Beleg protokolliert und ist damit
aufloesbar.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ab dieser Punktzahl gelten zwei Angebote als dasselbe Fahrzeug.
#
# Die Gewichte muessen die Schwelle OHNE gemeinsame Bilder erreichen koennen:
# AutoUncle speichert keine Bilder, und gerade dort sitzen die Dubletten zu
# mobile.de. Erreichbar sind ohne Bilder 0,90 – die Schwelle verlangt davon
# den Kilometerstand plus mindestens drei weitere uebereinstimmende Merkmale.
SCHWELLE_SICHER = 0.80
# Darunter, aber ueber dieser Grenze: moeglich, aber nicht automatisch mergen.
SCHWELLE_MOEGLICH = 0.60

# Toleranzen. Der Kilometerstand darf zwischen zwei Portalen leicht abweichen,
# weil die Angebote zu unterschiedlichen Zeitpunkten erfasst wurden.
# Absolut, nicht relativ: 2 % waeren bei 82.000 km ganze 1.640 km Spielraum –
# darin liegen beliebig viele verschiedene Fahrzeuge. Dasselbe Auto traegt auf
# zwei Portalen praktisch denselben Stand; 500 km decken Aktualisierungen ab.
KM_TOLERANZ_ABS = 500
PREIS_TOLERANZ_REL = 0.08


# Hersteller, die in den Titeln vorkommen. Ein Widerspruch hier schliesst eine
# Zusammenfuehrung sofort aus – ein ID.4 ist kein Q4 e-tron, auch wenn Baujahr,
# Kilometerstand und Leistung zufaellig passen.
_HERSTELLER = (
    "volkswagen", "vw", "audi", "bmw", "mercedes", "skoda", "seat", "cupra",
    "renault", "peugeot", "citroen", "opel", "ford", "tesla", "polestar",
    "volvo", "hyundai", "kia", "nissan", "toyota", "mazda", "honda", "fiat",
    "smart", "mini", "porsche", "jaguar", "mg", "byd", "nio", "dacia",
    "mitsubishi", "subaru", "suzuki", "jeep", "cadillac", "lexus", "genesis",
    "fisker", "aiways", "elaris", "ora", "maxus", "leapmotor", "xpeng",
)
_VW_SYNONYM = {"vw": "volkswagen"}


def _hersteller(titel: Optional[str]) -> set:
    """Erkennt genannte Hersteller im Titel (normalisiert)."""
    woerter = set(_norm(titel).split())
    gefunden = {_VW_SYNONYM.get(h, h) for h in _HERSTELLER if h in woerter}
    return gefunden


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _modellwoerter(titel: Optional[str]) -> set:
    """Aussagekraeftige Wortmarken aus dem Titel (ohne Fuellwoerter)."""
    # Ausstattungsbegriffe sind KEINE Modellkennung: "led" und "navi" hatten
    # sonst einen VW ID.4 mit einem Audi Q4 e-tron zusammengefuehrt.
    stop = {
        "neu", "gebraucht", "guter", "sehr", "preis", "und", "mit", "der", "die",
        "das", "elektro", "automatik", "km", "kwh", "ps", "kw", "euro", "inkl",
        "fairer", "superpreis", "seltenes", "fahrzeug", "angebot",
        # Ausstattung
        "led", "navi", "navigation", "acc", "shz", "sitzheizung", "kamera",
        "rfk", "pdc", "klima", "klimaautomatik", "pano", "panorama", "ahk",
        "carplay", "apple", "android", "bluetooth", "waermepumpe", "wärmepumpe",
        "matrix", "hud", "kessy", "assistenzpaket", "komfortpaket", "winterpaket",
        "alu", "lm", "sportsitze", "leder", "dab", "tempomat", "keyless",
    }
    woerter = {w for w in _norm(titel).split() if len(w) >= 3 and w not in stop}
    return {w for w in woerter if not w.isdigit()}


def _bildkennungen(roh) -> set:
    """Bild-IDs eines Angebots – identische Fotos sind ein starker Beleg."""
    if not roh:
        return set()
    try:
        urls = json.loads(roh) if isinstance(roh, str) else list(roh)
    except (ValueError, TypeError):
        return set()
    kennungen = set()
    for u in urls or []:
        m = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{20,})", str(u))
        if m:
            kennungen.add(m.group(1))
    return kennungen


def identitaets_score(a, b) -> Tuple[float, List[str]]:
    """Bewertet, ob zwei Angebote dasselbe Fahrzeug beschreiben.

    Liefert (Punktzahl 0..1, Belege). Widersprueche in harten Merkmalen setzen
    die Punktzahl auf 0 – lieber zwei Eintraege als eine falsche Verschmelzung.
    """
    belege: List[str] = []

    # --- harte Ausschluesse -------------------------------------------
    if a["year"] and b["year"] and a["year"] != b["year"]:
        return 0.0, ["Baujahr verschieden"]
    if a["fuel"] and b["fuel"] and _norm(a["fuel"]) != _norm(b["fuel"]):
        return 0.0, ["Kraftstoffart verschieden"]
    if a["power_ps"] and b["power_ps"] and abs(a["power_ps"] - b["power_ps"]) > 10:
        return 0.0, ["Leistung weicht deutlich ab"]

    marken_a, marken_b = _hersteller(a["title"]), _hersteller(b["title"])
    if marken_a and marken_b and not (marken_a & marken_b):
        return 0.0, ["Hersteller verschieden: %s / %s" % (
            ", ".join(sorted(marken_a)), ", ".join(sorted(marken_b)))]

    km_a, km_b = a["mileage"], b["mileage"]
    if km_a and km_b:
        if abs(km_a - km_b) > KM_TOLERANZ_ABS:
            return 0.0, ["Kilometerstand weicht zu stark ab"]

    # --- positive Belege ----------------------------------------------
    punkte = 0.0

    # Identische Bilder sind praktisch beweisend.
    gemeinsame_bilder = _bildkennungen(a["image_urls"]) & _bildkennungen(b["image_urls"])
    if gemeinsame_bilder:
        punkte += 0.55
        belege.append(f"{len(gemeinsame_bilder)} identische Bilder")

    if a["year"] and b["year"] and a["year"] == b["year"]:
        punkte += 0.12
        belege.append(f"Baujahr {a['year']}")
    if km_a and km_b:
        # Schaerfstes Merkmal: zwei verschiedene Autos haben praktisch nie
        # denselben Kilometerstand auf 2 % genau.
        punkte += 0.25
        belege.append(f"Kilometerstand {km_a} ~ {km_b}")
    if a["power_ps"] and b["power_ps"]:
        punkte += 0.10
        belege.append(f"Leistung {a['power_ps']} PS")

    gemeinsam = _modellwoerter(a["title"]) & _modellwoerter(b["title"])
    if len(gemeinsam) >= 2:
        punkte += 0.18
        belege.append("Modellwörter: " + ", ".join(sorted(gemeinsam)[:4]))
    elif gemeinsam:
        punkte += 0.05

    if a["location_zip"] and b["location_zip"] and a["location_zip"] == b["location_zip"]:
        punkte += 0.12
        belege.append(f"gleiche PLZ {a['location_zip']}")

    if a["price"] and b["price"]:
        if abs(a["price"] - b["price"]) <= max(200, a["price"] * PREIS_TOLERANZ_REL):
            punkte += 0.08
            belege.append("Preisnähe")

    # Akkuvariante als zusaetzliches Unterscheidungsmerkmal.
    for feld, name in (("battery_gross_kwh", "Akku brutto"), ("battery_net_kwh", "Akku netto")):
        if a[feld] and b[feld] and abs(a[feld] - b[feld]) < 0.6:
            punkte += 0.05
            belege.append(f"{name} {a[feld]:g} kWh")
            break

    return min(1.0, punkte), belege


def _portal_id(url: Optional[str]) -> Optional[str]:
    """Portaleigene Inserats-ID aus der URL, soweit erkennbar."""
    if not url:
        return None
    for muster in (r"[?&]id=(\d+)", r"/(\d{6,})(?:[-/?.]|$)", r"/de/d/(\d+)"):
        m = re.search(muster, url)
        if m:
            return m.group(1)
    return None


def _vehicle_id(row) -> str:
    """Stabile Kennung fuer ein neu angelegtes Fahrzeug."""
    return "v_" + str(row["fingerprint"])[:24]


_UEBERNAHME = (
    "year", "first_registration_month", "mileage", "power_ps", "fuel",
    "battery_net_kwh", "battery_gross_kwh", "ev_range_km", "ev_range_standard",
    "battery_soh", "battery_soh_level", "location", "location_zip", "distance_km",
)


def synchronisiere_fahrzeuge(store, limit: int = 500) -> dict:
    """Ueberfuehrt gespeicherte Inserate in Fahrzeuge und Angebote.

    Bestehende Zuordnungen bleiben erhalten; nur neue Angebote werden geprueft.
    Reine Datenbankarbeit, kein Netzwerkzugriff.
    """
    stats = {"geprueft": 0, "neue_fahrzeuge": 0, "zugeordnet": 0, "moeglich": 0}

    with store._lock:
        zeilen = store.conn.execute(
            "SELECT * FROM deals WHERE fingerprint NOT IN "
            "(SELECT offer_id FROM offers) LIMIT ?",
            (int(limit),),
        ).fetchall()
        if not zeilen:
            return stats

        # Vergleichsbestand als (vehicle_id, Inseratszeile). Waehrend des Laufs
        # neu angelegte Angebote werden ergaenzt, sonst koennen zwei Portale
        # desselben Fahrzeugs im selben Durchlauf nicht zusammenfinden.
        bestand = [
            (r["vehicle_id"], r)
            for r in store.conn.execute(
                "SELECT o.vehicle_id, d.* FROM offers o "
                "JOIN deals d ON d.fingerprint = o.offer_id"
            ).fetchall()
        ]

        jetzt = time.time()
        for row in zeilen:
            stats["geprueft"] += 1
            bester = None
            beste_punkte = 0.0
            beste_belege: List[str] = []
            for kandidat_vid, kandidat in bestand:
                if kandidat["portal"] == row["portal"]:
                    continue      # dasselbe Portal listet ein Auto nicht doppelt
                punkte, belege = identitaets_score(row, kandidat)
                if punkte > beste_punkte:
                    bester, beste_punkte, beste_belege = kandidat_vid, punkte, belege

            if bester is not None and beste_punkte >= SCHWELLE_SICHER:
                vid = bester
                stats["zugeordnet"] += 1
            else:
                vid = _vehicle_id(row)
                werte = [row[f] if f in row.keys() else None for f in _UEBERNAHME]
                store.conn.execute(
                    "INSERT OR REPLACE INTO vehicles (vehicle_id, %s, identity_confidence, created, updated) "
                    "VALUES (%s)" % (
                        ", ".join(_UEBERNAHME),
                        ", ".join(["?"] * (len(_UEBERNAHME) + 4)),
                    ),
                    (vid, *werte, beste_punkte, jetzt, jetzt),
                )
                stats["neue_fahrzeuge"] += 1
                if beste_punkte >= SCHWELLE_MOEGLICH:
                    stats["moeglich"] += 1

            store.conn.execute(
                "INSERT OR REPLACE INTO offers (offer_id, vehicle_id, portal, portal_id, title, "
                "price, location, url, image_urls, body, status, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'aktiv', ?, ?)",
                (row["fingerprint"], vid, row["portal"], _portal_id(row["url"]),
                 row["title"], row["price"], row["location"], row["url"],
                 row["image_urls"], row["body"], row["first_seen"], row["last_seen"]),
            )
            store.conn.execute(
                "INSERT OR REPLACE INTO vehicle_links (offer_id, vehicle_id, confidence, evidence, manual, created) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (row["fingerprint"], vid, beste_punkte,
                 "; ".join(beste_belege)[:400], jetzt),
            )
            bestand.append((vid, row))
        store.conn.commit()
    return stats
