"""Offline-Neuauswertung gespeicherter Inserate (K4 Phase 3).

Nach einer Parser-Änderung sind Altbestände veraltet: Felder, die eine neue
Erkennung füllen würde, bleiben leer. Statt die Portale erneut abzufragen –
was Budget kostet und Sperren riskiert – werden die bereits gespeicherten
Texte neu ausgewertet.

Jeder Datensatz trägt die ``detector_version``, mit der er erzeugt wurde.
Nur ältere Stände werden erneut verarbeitet.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Optional

from .models import DETECTOR_VERSION, Listing, infer_listing_details

logger = logging.getLogger(__name__)

# Felder, die aus dem gespeicherten Text neu abgeleitet werden.
_ABGELEITET = (
    "power_ps",
    "battery_kwh",
    "battery_observed_kind",
    "battery_net_kwh",
    "battery_gross_kwh",
    "battery_soh",
    "battery_soh_level",
    "ev_range_km",
    "ev_range_standard",
    "warranty",
    "location_zip",
    "location_city",
    "distance_km",
    "year_kind",
    "first_registration_month",
)


def _listing_aus_zeile(row) -> Listing:
    """Baut ein Listing aus einer gespeicherten Zeile – nur Rohdaten."""
    spalten = row.keys()

    def w(name, default=None):
        return row[name] if name in spalten else default

    bilder = []
    roh = w("image_urls")
    if roh:
        try:
            bilder = json.loads(roh) if isinstance(roh, str) else list(roh)
        except (ValueError, TypeError):
            bilder = []

    return Listing(
        portal=w("portal") or "",
        title=w("title") or "",
        url=w("url") or "",
        price=w("price"),
        year=w("year"),
        mileage=w("mileage"),
        fuel=w("fuel"),
        location=w("location"),
        power_ps=w("power_ps"),
        body=w("body"),
        image_urls=bilder,
    )


def reevaluate_stored_listings(store, limit: int = 200,
                               home_zip: Optional[str] = None) -> dict:
    """Wertet gespeicherte Inserate mit älterer Parser-Version neu aus.

    Führt keine Netzwerkzugriffe aus. Liefert eine Statistik, welche Felder
    dadurch neu belegt werden konnten.
    """
    stats: Counter = Counter()
    with store._lock:
        zeilen = store.conn.execute(
            "SELECT * FROM deals "
            "WHERE detector_version IS NULL OR detector_version != ? "
            "LIMIT ?",
            (DETECTOR_VERSION, int(limit)),
        ).fetchall()

    if not zeilen:
        return {"geprueft": 0}

    for row in zeilen:
        stats["geprueft"] += 1
        try:
            listing = _listing_aus_zeile(row)
            # check_images bleibt aus: OCR gehört nicht in diesen Lauf.
            infer_listing_details(listing, home_zip)
        except Exception:
            logger.exception("Neuauswertung fehlgeschlagen für %s", row["fingerprint"])
            stats["fehler"] += 1
            continue

        felder = row.keys()
        setzt = {}
        for name in _ABGELEITET:
            if name not in felder:
                continue
            neu = getattr(listing, name, None)
            alt = row[name]
            leer_vorher = alt in (None, "", 0, "unbekannt")
            if neu not in (None, "", "unbekannt") and neu != alt:
                setzt[name] = neu
                if leer_vorher:
                    stats[f"neu:{name}"] += 1

        setzt["detector_version"] = DETECTOR_VERSION
        zuweisung = ", ".join(f"{k} = ?" for k in setzt)
        with store._lock:
            store.conn.execute(
                f"UPDATE deals SET {zuweisung} WHERE fingerprint = ?",
                (*setzt.values(), row["fingerprint"]),
            )
        stats["aktualisiert"] += 1

    with store._lock:
        store.conn.commit()
    return dict(stats)
