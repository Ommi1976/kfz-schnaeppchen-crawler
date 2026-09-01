"""Gemeinsame Datenmodelle für alle Portale."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional


PS_PER_KW = 1.35962   # Umrechnung kW -> PS
DETECTOR_VERSION = "1.2.0"


_BATTERY_KWH_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*k\s*wh\b(?!\s*(?:/\s*100|pro\s*100|/100km|/km))",
    re.IGNORECASE,
)
_EV_RANGE_RE = re.compile(
    r"(?<!gesamt)(?:reichweite|range)\D{0,24}(\d{2,4})\s*km|"
    r"(\d{2,4})\s*km\D{0,24}(?<!gesamt)(?:reichweite|range)",
    re.IGNORECASE,
)
# Stichwörter, die eindeutig den Akku-Gesundheitszustand meinen
_SOH_STRONG = (
    r"(?:soh|state\s+of\s+health|health\s+state|battery\s+health|batteriegesundheit|akkugesundheit"
    r"|gesundheitszustand|batteriezustand|akkuzustand|hv\s*-\s*batteriezustand|hv\s*-\s*batteriegesundheit"
    r"|soh\s*-\s*wert|soh\s*-\s*score|zertifikatswert|battery\s+degradation"
    r"|aviloo|dekra|t[üu]v\s+(?:rheinland|s[üu]d|nord|batterietest))"
)
# Alle Stichwörter inkl. der mehrdeutigen (Kapazität/Status/Check)
_SOH_ANY = (
    r"(?:soh|state\s+of\s+health|health\s+state|battery\s+health|battery\s+degradation"
    r"|batterie(?:-?\s*(?:status|information|gesundheit|zustand|zertifikat|test|check|kapazit[aä]t))"
    r"|akku(?:-?\s*(?:status|gesundheit|zustand|zertifikat|test|check|kapazit[aä]t))"
    r"|hv\s*-\s*batterie(?:-?\s*(?:status|gesundheit|zustand|zertifikat|test|check))"
    r"|gesundheitszustand|restkapazit[aä]t|verbleibende\s+kapazit[aä]t|zertifikatswert|soh\s*-\s*wert"
    r"|zertifizierte\s+(?:rest)?kapazit[aä]t"
    r"|aviloo(?:\s*-\s*score|\s+score|\s+zertifikat|\s+test|\s+flash|\s+premium)?"
    r"|dekra(?:\s+batterietest|\s+zertifikat|\s+test|\s+siegel)?"
    r"|t[üu]v(?:\s+rheinland|\s+s[üu]d|\s+nord)?(?:\s+batterietest|\s+battery\s+quick\s+check|\s+zertifikat|\s+test)?)"
)
_BATTERY_SOH_RE = re.compile(
    # 1) Stichwort … Zahl % (z.B. "Batteriezustand lt. Test vom 12.03.2024: 92 %", "SoH: 94.6 %")
    _SOH_ANY + r"[\s\S]{0,60}?(\d{2,3}(?:[.,]\d+)?)\s*%"
    # 2) Zahl % … Stichwort (z. B. "92,4 % (SoH)", "96% State of Health")
    r"|(\d{2,3}(?:[.,]\d+)?)\s*%[\s\S]{0,35}?" + _SOH_ANY +
    # 3) Eindeutiges Stichwort / Aviloo Score ohne % (z. B. "SoH 92", "Aviloo Score: 96", "Aviloo: 98/100")
    r"|" + _SOH_STRONG + r"(?:\s*-\s*score|\s+score)?\s*[:=)\]}]?\s*(\d{2,3}(?:[.,]\d+)?)(?:\s*/\s*100)?(?!\s*(?:k?wh|kw|km|ps|€|eur))"
    # 4) Akku bei/noch XX %
    r"|(?:akku|batterie|hv\s*-\s*batterie)\s+(?:liegt\s+)?(?:bei|mit|noch)\s+(\d{2,3}(?:[.,]\d+)?)\s*%"
    # 5) Bewertungswidget: Prozentwert unmittelbar mit Qualitätsurteil. Das ist
    # ein etablierter SoH-Hinweis, während nackte Prozentwerte ausgeschlossen sind.
    r"|(\d{2,3}(?:[.,]\d+)?)\s*%\s*(?:sehr\s*gut|gut|ausgezeichnet|top)\b",
    re.IGNORECASE,
)


def extract_battery_kwh(text: str | None) -> Optional[float]:
    """Erkennt eine Akku-Kapazität aus Inseratstexten, z. B. ``62 kWh``."""
    if not text:
        return None
    values = []
    for match in _BATTERY_KWH_RE.finditer(str(text)):
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if 15.0 <= value <= 130.0:
            values.append(value)
    # Nicht den größten Wert nehmen: Vollseiten enthalten oft Vergleichsangebote
    # mit anderen Akkugrößen. Die Reihenfolge des eng begrenzten Textausschnitts
    # ist zuverlässiger als ein Maximalwert.
    return values[0] if values else None


def extract_battery_soh(text: str | None) -> Optional[float]:
    """Erkennt den State of Health (SoH in %) aus Texten, z. B. ``Batterie-Status 94.6%``, ``SOH 96%`` oder ``Aviloo 98%``."""
    if not text:
        return None
    for match in _BATTERY_SOH_RE.finditer(str(text)):
        for val in match.groups():
            if val:
                try:
                    num = float(val.replace(",", "."))
                    if 50.0 <= num <= 100.0 and num != 19.0:
                        return round(num, 1)
                except ValueError:
                    continue
    return None


def extract_ev_range_km(text: str | None) -> Optional[int]:
    """Erkennt eine elektrische Reichweite aus Texten wie ``455 km Reichweite``."""
    if not text:
        return None
    for match in _EV_RANGE_RE.finditer(str(text)):
        value = match.group(1) or match.group(2)
        if value:
            number = int(value)
            if 50 <= number <= 1500:
                return number
    return None


# Modell-Katalog bekannter Elektrofahrzeuge: (Regex, Akku brutto/netto kWh, Reichweite WLTP km)
_KNOWN_EV_CATALOG = [
    # VW ID.3
    (re.compile(r"\bid\.?3\b.*?\b(?:pure\s+performance|pure)\b", re.I), 55.0, 350),
    (re.compile(r"\bid\.?3\b.*?\b(?:pro\s*s|pro-s)\b", re.I), 82.0, 550),
    (re.compile(r"\bid\.?3\b.*?\b(?:pro\s+performance|pro)\b", re.I), 62.0, 425),
    (re.compile(r"\bid\.?3\b", re.I), 58.0, 420),
    # VW ID.4 / ID.5
    (re.compile(r"\bid\.?[45]\b.*?\b(?:pure)\b", re.I), 55.0, 360),
    (re.compile(r"\bid\.?[45]\b.*?\b(?:pro\s*s|pro-s)\b", re.I), 82.0, 530),
    (re.compile(r"\bid\.?[45]\b.*?\b(?:pro\s+performance|pro|gtx|1st)\b", re.I), 82.0, 520),
    (re.compile(r"\bid\.?[45]\b", re.I), 77.0, 500),
    # Cupra Born: 170 kW / e-Boost ist bei mehreren Akkuvarianten möglich;
    # nur eine explizite Kapazität ist für eine automatische Zuordnung sicher.
    (re.compile(r"\bborn\b.*?\b(?:45|55)\s*kwh\b", re.I), 55.0, 340),
    (re.compile(r"\bborn\b.*?\b(?:58|60|62|63)\s*kwh\b", re.I), 62.0, 425),
    (re.compile(r"\bborn\b.*?\b(?:77|79|82|84)\s*kwh\b", re.I), 82.0, 548),
    # Skoda Enyaq
    (re.compile(r"\benyaq\b.*?\b(?:80|80x|85|rs|82)\b", re.I), 82.0, 535),
    (re.compile(r"\benyaq\b.*?\b(?:60)\b", re.I), 62.0, 400),
    (re.compile(r"\benyaq\b.*?\b(?:50)\b", re.I), 55.0, 350),
    (re.compile(r"\benyaq\b", re.I), 77.0, 500),
    # Audi Q4
    (re.compile(r"\bq4\b.*?\b(?:35)\b", re.I), 55.0, 340),
    (re.compile(r"\bq4\b.*?\b(?:40|45|50|55)\b", re.I), 82.0, 520),
    (re.compile(r"\bq4\b", re.I), 77.0, 500),
    # Tesla Model 3 / Y
    (re.compile(r"\bmodel\s*3\b.*?\b(?:long\s*range|maximale\s*reichweite|dual\s*motor|performance)\b", re.I), 78.5, 602),
    (re.compile(r"\bmodel\s*3\b.*?\b(?:standard|range\s*plus|sr\+|rwd)\b", re.I), 60.0, 491),
    (re.compile(r"\bmodel\s*3\b", re.I), 60.0, 491),
    (re.compile(r"\bmodel\s*y\b.*?\b(?:long\s*range|maximale\s*reichweite|dual\s*motor|performance)\b", re.I), 78.5, 533),
    (re.compile(r"\bmodel\s*y\b.*?\b(?:standard|rwd)\b", re.I), 60.0, 455),
    (re.compile(r"\bmodel\s*y\b", re.I), 75.0, 500),
    # Mercedes EQ
    (re.compile(r"\beqa\s*250\+", re.I), 70.5, 530),
    (re.compile(r"\beqa\s*(?:250|300|350)\b", re.I), 66.5, 430),
    (re.compile(r"\beqb\s*(?:250|300|350)\b", re.I), 66.5, 420),
    (re.compile(r"\beqe\s*(?:300|350)\b", re.I), 89.0, 620),
    (re.compile(r"\beqe\s*(?:43|53|500)\b", re.I), 90.6, 500),
    (re.compile(r"\beqc\s*400\b", re.I), 80.0, 415),
    # BMW
    (re.compile(r"\bi3\s*s?\b.*?\b120\s*ah\b", re.I), 42.2, 305),
    (re.compile(r"\bi3\s*s?\b.*?\b94\s*ah\b", re.I), 33.2, 255),
    (re.compile(r"\bi3\s*s?\b.*?\b60\s*ah\b", re.I), 22.0, 190),
    (re.compile(r"\bix3\b", re.I), 80.0, 460),
    (re.compile(r"\bi4\b.*?\b(?:edrive35)\b", re.I), 70.2, 480),
    (re.compile(r"\bi4\b.*?\b(?:edrive40|m50)\b", re.I), 83.9, 585),
    (re.compile(r"\bix1\b", re.I), 64.7, 440),
    # Hyundai / Kia
    (re.compile(r"\bioniq\s*5\b.*?\b(?:77|77\.4|72\.6|84)\b", re.I), 77.4, 480),
    (re.compile(r"\bioniq\s*5\b.*?\b(?:58)\b", re.I), 58.0, 384),
    (re.compile(r"\bioniq\s*5\b", re.I), 72.6, 450),
    (re.compile(r"\bioniq\s*6\b", re.I), 77.4, 614),
    (re.compile(r"\bkona\b.*?\b(?:64|65\.4|150\s*kw|204\s*ps)\b", re.I), 64.0, 484),
    (re.compile(r"\bkona\b.*?\b(?:39|39\.2|100\s*kw|136\s*ps)\b", re.I), 39.2, 305),
    (re.compile(r"\bev6\b", re.I), 77.4, 528),
    (re.compile(r"\be-niro\b|\bniro\s*ev\b", re.I), 64.8, 460),
    # Renault / Fiat / Sonstige
    (re.compile(r"\bmegane\b.*?\b(?:ev60|60\s*kwh|220\s*ps)\b", re.I), 60.0, 450),
    (re.compile(r"\bmegane\b.*?\b(?:ev40|40\s*kwh|130\s*ps)\b", re.I), 40.0, 300),
    (re.compile(r"\bzoe\b.*?\b(?:ze\s*50|r110|r135|52)\b", re.I), 52.0, 390),
    (re.compile(r"\bzoe\b.*?\b(?:ze\s*40|41)\b", re.I), 41.0, 300),
    (re.compile(r"\b500e\b|\bfiat\s*500\s*e\b", re.I), 42.0, 320),
    (re.compile(r"\bsmart\s*#1\b", re.I), 66.0, 440),
    (re.compile(r"\bsmart\s*eq\b|\bfortwo\s*eq\b|\bforfour\s*eq\b", re.I), 17.6, 135),
    (re.compile(r"\bora\s*(?:03|funky\s*cat)\b.*?\b400\b", re.I), 63.0, 420),
    (re.compile(r"\bora\s*(?:03|funky\s*cat)\b", re.I), 48.0, 310),
    (re.compile(r"\bnissan\s*leaf\b.*?\b(?:e\+|62)\b", re.I), 62.0, 385),
    (re.compile(r"\bnissan\s*leaf\b", re.I), 40.0, 270),
    (re.compile(r"\bpeugeot\s*e-?208\b|\bopel\s*corsa-?e\b", re.I), 50.0, 360),
    (re.compile(r"\bpeugeot\s*e-?2008\b|\bopel\s*mokka-?e\b", re.I), 50.0, 340),
    (re.compile(r"\b(?:byd\b.*?\bdolphin\b.*?\b(?:surf|active|boost)\b|dolphin\s*surf\b|\bsurf\s*comfort\b)", re.I), 44.9, 310),
    (re.compile(r"\b(?:byd\b.*?\bdolphin\b|\bdolphin\b)", re.I), 60.4, 427),
    (re.compile(r"\b(?:byd\b.*?\batto\s*3\b|\batto\s*3\b)", re.I), 60.5, 420),
    (re.compile(r"\b(?:byd\b.*?\bseal\b|\bseal\b)", re.I), 82.5, 570),
    (re.compile(r"\bex30\b.*?\b(?:single\s+motor\b(?!.*?extended)|core\b(?!.*?extended)|51\s*kwh)\b", re.I), 51.0, 344),
    (re.compile(r"\bex30\b", re.I), 69.0, 476),
    (re.compile(r"\bev3\b.*?\b(?:standard|58\s*kwh)\b", re.I), 58.3, 436),
    (re.compile(r"\bev3\b", re.I), 81.4, 605),
    (re.compile(r"\bmini\b.*?\b(?:cooper\s*se|cooper-se)\b|\bcooper\s*se\b", re.I), 32.6, 233),
    (re.compile(r"\bmini\b.*?\bcooper\s*e\b", re.I), 40.7, 305),
    (re.compile(r"\bcountryman\s*(?:se|all4)\b", re.I), 66.5, 433),
    (re.compile(r"\bcountryman\s*e\b", re.I), 66.5, 462),
    (re.compile(r"\bmach-?e\b.*?\b(?:er|extended|98|91|awd|4x)\b", re.I), 98.7, 600),
    (re.compile(r"\bmach-?e\b", re.I), 75.7, 440),
    (re.compile(r"\bscenic\b.*?\b(?:ev87|87\s*kwh|220\s*ps)\b", re.I), 87.0, 625),
    (re.compile(r"\bscenic\b", re.I), 60.0, 430),
    (re.compile(r"\be-?(?:3008|5008)\b.*?\b(?:long\s*range|98\s*kwh|700\s*km)\b", re.I), 98.0, 700),
    (re.compile(r"\be-?(?:3008|5008)\b", re.I), 73.0, 525),
    (re.compile(r"\bariya\b.*?\b(?:87|e-4orce|evolve|242\s*ps)\b", re.I), 87.0, 533),
    (re.compile(r"\bariya\b", re.I), 63.0, 403),
    (re.compile(r"\bbz4x\b|\bsolterra\b|\brz\s*450e\b", re.I), 71.4, 513),
    (re.compile(r"\b(?:gv60|gv70)\b", re.I), 77.4, 517),
    (re.compile(r"\bg80\b", re.I), 87.2, 520),
    (re.compile(r"\btaycan\b.*?\b(?:plus|4s|turbo|gts|105|93)\b", re.I), 93.4, 505),
    (re.compile(r"\btaycan\b", re.I), 79.2, 430),
    (re.compile(r"\bet5\b|\bet7\b|\b(?:el6|el7|el8)\b", re.I), 100.0, 580),
    (re.compile(r"\bocean\b|\bfisker\b", re.I), 113.0, 707),
    (re.compile(r"\blucid\b|\blucid\s*air\b", re.I), 112.0, 725),
    (re.compile(r"\bvf\s*8\b|\bvinfast\b", re.I), 87.7, 471),
    (re.compile(r"\bspring\b|\bdacia\s+spring\b", re.I), 26.8, 230),
]


def infer_ev_specs_from_model(text: str | None) -> tuple[Optional[float], Optional[int]]:
    """Gibt EV-Daten ausschließlich aus der zentralen Referenzdatenbank zurück."""
    if not text:
        return None, None
    try:
        from kfz_crawler.ev_database import lookup_ev_spec
        spec = lookup_ev_spec(text)
        if spec:
            return spec.battery_gross_kwh, spec.wltp_range_km
    except Exception:
        pass
    return None, None


def _record_evidence(
    listing: "Listing",
    field_name: str,
    source: str,
    confidence: float,
    evidence: str = "",
) -> None:
    listing.field_evidence[field_name] = {
        "source": source,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": evidence[:240],
        "detector_version": DETECTOR_VERSION,
    }


def ensure_portal_evidence(listing: "Listing") -> None:
    """Dokumentiert strukturierte Portalwerte, bevor Ableitungen hinzukommen."""
    for field_name in (
        "price", "year", "mileage", "fuel", "power_ps", "transmission",
        "location", "body_type",
    ):
        if getattr(listing, field_name, None) is not None and field_name not in listing.field_evidence:
            _record_evidence(listing, field_name, "portal_structured", 0.96, "Strukturiertes Trefferfeld")


# Wortmarken, mit denen Inserate die Bezugsgröße benennen.
_NETTO_MARKER = re.compile(r"\b(netto|nutzbar\w*|verf[üu]gbar\w*|usable)\b", re.I)
_BRUTTO_MARKER = re.compile(r"\b(brutto|gesamt\w*|total)\b", re.I)


def classify_battery_kind(text: str | None, value: Optional[float],
                          spec_net: Optional[float] = None,
                          spec_gross: Optional[float] = None) -> str:
    """Bestimmt, ob ein gelesener kWh-Wert netto oder brutto meint.

    Reihenfolge: ausdrückliche Wortmarke im Text, sonst Abgleich mit den
    Referenzwerten des Modells. Ohne beides bleibt es "unbekannt" – geraten
    wird nicht, weil eine falsche Bezugsgröße Filter kippen lässt.
    """
    if value is None:
        return "unbekannt"
    if text:
        # Eine Wortmarke gehört zu der Zahl, neben der sie steht. Das Fenster
        # endet deshalb an der nächsten kWh-Angabe – sonst würde bei
        # "77 kWh netto (82 kWh brutto)" die Marke der Nachbarzahl gelesen.
        treffer = list(re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*k\s*wh", text, re.I))
        for i, m in enumerate(treffer):
            try:
                if abs(float(m.group(1).replace(",", ".")) - value) > 0.6:
                    continue
            except ValueError:
                continue
            nach_ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
            vor_start = treffer[i - 1].end() if i else 0
            danach = text[m.end():min(nach_ende, m.end() + 20)]
            davor = text[max(vor_start, m.start() - 25):m.start()]
            for umfeld in (danach, davor):
                if _NETTO_MARKER.search(umfeld):
                    return "netto"
                if _BRUTTO_MARKER.search(umfeld):
                    return "brutto"
    if spec_net is not None and abs(value - spec_net) <= 1.0:
        return "netto"
    if spec_gross is not None and abs(value - spec_gross) <= 1.0:
        return "brutto"
    return "unbekannt"


# Belegstufen fuer den Batteriezustand.
_SOH_ZERTIFIKAT = re.compile(
    r"\b(aviloo|dekra|t[uü]v|batteriezertifikat|pr[uü]fbericht|gutachten|batterietest)\b",
    re.I,
)
_SOH_AUSDRUECKLICH = re.compile(
    r"\b(soh|state\s+of\s+health|batteriezustand|batteriegesundheit|akkugesundheit|gesundheitszustand|batterie-?status|restkapazit(?:ae|[aä])t)\b",
    re.I,
)


def classify_soh_level(text: str | None, value: Optional[float]) -> str:
    """Bewertet, wie belastbar eine SoH-Angabe ist.

    Ein Prozentwert allein sagt wenig: er koennte auch ein Ladestand sein.
    Erst ein Zertifikatsbeleg oder eine ausdrueckliche SoH-Benennung macht
    ihn belastbar. Die Stufe steuert, ob hart gefiltert werden darf.
    """
    if value is None:
        return "unbekannt"
    if not text:
        return "kandidat"
    if _SOH_ZERTIFIKAT.search(text):
        return "bestaetigt"
    if _SOH_AUSDRUECKLICH.search(text):
        return "belegt"
    return "kandidat"


def infer_listing_battery(listing: "Listing", check_images: bool = False) -> None:
    """Füllt den Akkuwert und SoH nach:
    1. Priorität: Interne Referenzdatenbank (ev_database) für verifizierte Brutto-/Nettowerte.
    2. Plausibilisierung: Explizite Händlerwerte werden mit der Referenzdatenbank abgeglichen.
    """
    title = listing.title or ""
    detail_text = getattr(listing, "body", "") or ""
    title_kwh = extract_battery_kwh(title)
    detail_kwh = extract_battery_kwh(detail_text)

    match = None
    try:
        from kfz_crawler.ev_database import lookup_ev_spec_match
        match = lookup_ev_spec_match(title, detail_text, power_ps=listing.power_ps)
    except Exception:
        pass

    if match:
        spec = match.spec
        listing.battery_gross_kwh = spec.battery_gross_kwh
        listing.battery_net_kwh = spec.battery_net_kwh
        _record_evidence(
            listing,
            "battery_reference",
            "ev_database",
            match.confidence,
            f"{spec.make} {spec.model} {spec.variant}; {match.evidence}",
        )

    explicit_kwh = title_kwh if title_kwh is not None else detail_kwh
    if match and match.confidence >= 0.90:
        # Der bisherige Einzelwert bleibt die tatsächlich gelesene Angabe. Für
        # Filter steht zusätzlich die eindeutige Bruttokapazität bereit.
        listing.battery_kwh = explicit_kwh if explicit_kwh is not None else match.spec.battery_gross_kwh
        source = "title" if title_kwh is not None else "detail_text" if detail_kwh is not None else "ev_database"
        confidence = 0.98 if explicit_kwh is not None else match.confidence
        _record_evidence(listing, "battery_kwh", source, confidence, match.evidence)
    elif title_kwh is not None:
        listing.battery_kwh = title_kwh
        _record_evidence(listing, "battery_kwh", "title", 0.99, f"{title_kwh:g} kWh")
    elif detail_kwh is not None:
        listing.battery_kwh = detail_kwh
        _record_evidence(listing, "battery_kwh", "detail_text", 0.88, f"{detail_kwh:g} kWh")

    # Ohne Bezugsgroesse ist der gelesene Wert nicht sicher vergleichbar:
    # netto und brutto liegen typisch fuenf kWh auseinander.
    if listing.battery_kwh is not None:
        listing.battery_observed_kind = classify_battery_kind(
            f"{title} {detail_text}",
            listing.battery_kwh,
            listing.battery_net_kwh,
            listing.battery_gross_kwh,
        )
        # Ist die Groesse bekannt, wird der Wert in das passende Feld
        # uebernommen, sofern die Referenzdatenbank dort nichts geliefert hat.
        if listing.battery_observed_kind == "netto" and listing.battery_net_kwh is None:
            listing.battery_net_kwh = listing.battery_kwh
        elif listing.battery_observed_kind == "brutto" and listing.battery_gross_kwh is None:
            listing.battery_gross_kwh = listing.battery_kwh
    elif match:
        listing.battery_kwh = match.spec.battery_gross_kwh
        _record_evidence(listing, "battery_kwh", "ev_database", match.confidence, match.evidence)
    elif listing.battery_kwh is not None:
        _record_evidence(listing, "battery_kwh", "portal_structured", 0.82, "Portalwert")

    if listing.battery_soh is None:
        title_soh = extract_battery_soh(title)
        detail_soh = extract_battery_soh(detail_text)
        listing.battery_soh = title_soh if title_soh is not None else detail_soh
        if listing.battery_soh is not None:
            source = "title" if title_soh is not None else "detail_text"
            _record_evidence(listing, "battery_soh", source, 0.98 if title_soh is not None else 0.93, f"SoH {listing.battery_soh:g} %")
        if listing.battery_soh is None and check_images and getattr(listing, "image_urls", None):
            try:
                from kfz_crawler.battery_analyzer import extract_soh_from_image_urls
                listing.battery_soh = extract_soh_from_image_urls(listing.image_urls)
                if listing.battery_soh is not None:
                    _record_evidence(listing, "battery_soh", "ocr_consensus", 0.86, "Bild-/Dokumentanalyse")
            except Exception:
                pass

    # Belegstufe erst bewerten, wenn der SoH feststeht: ein Prozentwert ohne
    # Batteriekontext koennte auch ein Ladestand sein.
    if listing.battery_soh is not None and listing.battery_soh_level == "unbekannt":
        listing.battery_soh_level = classify_soh_level(
            f"{title} {detail_text}", listing.battery_soh
        )


# Messstandards fuer Reichweiten. Reihenfolge = Erkennungsprioritaet.
_RANGE_STANDARDS = (
    ("wltp", re.compile(r"\bwltp\b", re.I)),
    ("nefz", re.compile(r"\b(nefz|nedc)\b", re.I)),
    ("epa", re.compile(r"\bepa\b", re.I)),
    ("real", re.compile(r"\b(real\w*|praxis\w*|alltag\w*)\b", re.I)),
)


def classify_range_standard(text: str | None, value: Optional[int]) -> str:
    """Bestimmt den Messstandard einer Reichweitenangabe aus dem Text.

    Wie bei der Akku-Bezugsgroesse zaehlt nur die Umgebung der Zahl, damit
    ein Standard nicht von einer anderen Angabe geliehen wird. Ohne Marke
    bleibt es "unbekannt".
    """
    if value is None or not text:
        return "unbekannt"
    treffer = list(re.finditer(r"(\d{2,4})\s*km", text, re.I))
    for i, m in enumerate(treffer):
        try:
            if int(m.group(1)) != int(value):
                continue
        except ValueError:
            continue
        nach_ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        vor_start = treffer[i - 1].end() if i else 0
        danach = text[m.end():min(nach_ende, m.end() + 30)]
        # Im Davor-Fenster nur bis zum letzten Satzzeichen zurueckgehen: eine
        # Marke hinter der Vorgaengerzahl ("520 km WLTP, Anhaengelast 750 km")
        # gehoert nicht zu dieser Angabe.
        davor = text[max(vor_start, m.start() - 30):m.start()]
        # Nur Komma und Semikolon trennen Angaben. Doppelpunkt und Klammern
        # gehoeren zum Label selbst: "Reichweite (WLTP) 546 km".
        davor = re.split(r"[,;]", davor)[-1]
        for umfeld in (danach, davor):
            for name, muster in _RANGE_STANDARDS:
                if muster.search(umfeld):
                    return name
    return "unbekannt"


# Plausibilitaetsrahmen fuer Reichweite je kWh (Bruttokapazitaet).
#
# Gemessen an 144 Inseraten mit bekanntem Akku: 5,13 bis 7,67 km/kWh, Median
# 6,55. Die Grenzen hier sind bewusst weiter gesetzt, damit nur eindeutige
# Fehler auffallen und keine ungewoehnlichen, aber echten Fahrzeuge.
KM_JE_KWH_MIN = 3.8
KM_JE_KWH_MAX = 8.2


def range_plausibel(range_km: Optional[int], akku_kwh: Optional[float]) -> Optional[bool]:
    """Passt eine Reichweite zur Akkugroesse? None, wenn nicht pruefbar.

    Haeufigster Fehlerfall: Portale nennen einen Modellwert, der zur
    groesseren Akkuvariante gehoert. Ein Cupra Born mit 58 kWh und angeblich
    555 km waeren 9 km/kWh – das gibt es nicht.
    """
    if not range_km or not akku_kwh:
        return None
    return KM_JE_KWH_MIN <= (range_km / akku_kwh) <= KM_JE_KWH_MAX


def infer_listing_range(listing: "Listing") -> None:
    """Füllt die elektrische Reichweite nach:
    1. Priorität: Interne Referenzdatenbank (ev_database) mit offiziellem WLTP-Kombiniert-Wert.
    2. Plausibilisierung: Händler-Übertreibungen (z. B. City-WLTP) werden durch echten WLTP Kombiniert korrigiert.
    """
    title = listing.title or ""
    detail_text = getattr(listing, "body", "") or ""
    title_rng = extract_ev_range_km(title)
    detail_rng = extract_ev_range_km(detail_text)
    explicit_rng = title_rng if title_rng is not None else detail_rng

    match = None
    try:
        from kfz_crawler.ev_database import lookup_ev_spec_match
        match = lookup_ev_spec_match(title, detail_text, power_ps=listing.power_ps)
    except Exception:
        pass

    if match:
        spec = match.spec
        # Wenn der Händlerwert deutlich über dem echten WLTP-Kombiniert-Wert liegt (z. B. 460 km vs. 310 km),
        # wird der offizielle WLTP-Kombiniert-Wert aus der Datenbank verwendet.
        if explicit_rng is not None:
            if explicit_rng > spec.wltp_range_km * 1.05:
                listing.ev_range_km = spec.wltp_range_km
                listing.ev_range_standard = "wltp"
                _record_evidence(listing, "ev_range_km", "ev_database", match.confidence, "Inseratswert deutlich über WLTP")
            else:
                listing.ev_range_km = explicit_rng
                _record_evidence(listing, "ev_range_km", "title" if title_rng is not None else "detail_text", 0.93, "Reichweitenangabe im Inserat")
        else:
            listing.ev_range_km = spec.wltp_range_km
            listing.ev_range_standard = "wltp"
            _record_evidence(listing, "ev_range_km", "ev_database", match.confidence, f"WLTP {spec.variant}")
    else:
        if explicit_rng is not None:
            listing.ev_range_km = explicit_rng
            _record_evidence(listing, "ev_range_km", "title" if title_rng is not None else "detail_text", 0.91, "Reichweitenangabe im Inserat")
        elif listing.ev_range_km is not None:
            _record_evidence(listing, "ev_range_km", "portal_structured", 0.82, "Portalwert")

    # Gegenprobe an der Akkugröße. Portale nennen häufig einen Modellwert, der
    # zur größeren Akkuvariante gehört – bei mehrdeutigen Titeln ("Born 231 PS")
    # sind das über 100 km Unterschied. Passt die Reichweite nicht zum Akku,
    # wird der Referenzwert des Modells vorgezogen, sofern der stimmig ist.
    akku = listing.battery_gross_kwh or listing.battery_kwh
    if range_plausibel(listing.ev_range_km, akku) is False:
        referenz = match.spec.wltp_range_km if match else None
        if range_plausibel(referenz, akku):
            _record_evidence(
                listing, "ev_range_km", "ev_database", 0.80,
                f"Inseratswert {listing.ev_range_km} km unplausibel für {akku:g} kWh",
            )
            listing.ev_range_km = referenz
            listing.ev_range_standard = "wltp"
        else:
            # Kein belastbarer Ersatz: lieber als unsicher kennzeichnen, als
            # eine Zahl zu zeigen, die zum Fahrzeug nicht passen kann.
            listing.ev_range_standard = "unplausibel"
            _record_evidence(
                listing, "ev_range_km", "plausibilitaet", 0.30,
                f"{listing.ev_range_km} km passen nicht zu {akku:g} kWh",
            )


    # Stammt der Wert aus dem Inseratstext, wird der Messstandard dort gesucht.
    # Ohne Marke bleibt "unbekannt" – eine geschätzte Reichweite darf nicht wie
    # eine offizielle Angabe wirken.
    if listing.ev_range_km is not None and listing.ev_range_standard == "unbekannt":
        listing.ev_range_standard = classify_range_standard(
            f"{title} {detail_text}", listing.ev_range_km
        )


_WARRANTY_PATTERNS = [
    re.compile(r"\b(\d{1,2}\s*(?:Jahre?|J\.)\s*/\s*(?:\d{2,3}(?:\.000|\s*tkm|\s*k)?\s*km))\b", re.I),
    re.compile(r"\b(?:batterie-?garantie|herstellergarantie|werksgarantie)\s*[:=]?\s*([^\n\r<,;]{3,35})", re.I),
    re.compile(r"\b(garantie\s+bis\s+\d{2}/\d{4})\b", re.I),
    re.compile(r"\b(\d{1,2}\s*Monate?\s*(?:Gebrauchtwagen-?|Hersteller-?|Werks-?)?Garantie)\b", re.I),
    re.compile(r"\b(\d{1,2}\s*Jahre?\s*(?:Gebrauchtwagen-?|Hersteller-?|Werks-?)?Garantie)\b", re.I),
    re.compile(r"\b(12\s*Monate\s*Garantie|24\s*Monate\s*Garantie|36\s*Monate\s*Garantie)\b", re.I),
    re.compile(r"\b(Herstellergarantie|Werksgarantie|Gebrauchtwagengarantie)\b", re.I),
]


def extract_warranty(text: str | None) -> Optional[str]:
    """Erkennt Batterie- oder Fahrzeuggarantien aus Texten."""
    if not text:
        return None
    for pat in _WARRANTY_PATTERNS:
        m = pat.search(str(text))
        if m:
            val = m.group(1).strip()
            if "nicht" in val.lower() or "kein" in val.lower() or len(val) < 3:
                continue
            return val
    return None


_EZ_RE = re.compile(
    r"(?:\bEZ\b|erstzulassung|zulassung)\s*[:.]?\s*(?:(\d{1,2})\s*[./]\s*)?(\d{4})",
    re.I,
)
_MODELLJAHR_RE = re.compile(r"(?:modelljahr|\bmj\b|baujahr)\s*[:.]?\s*(\d{4})", re.I)


def extract_first_registration(text: str | None):
    """Liefert (Jahr, Monat, Art) fuer die Zulassungsangabe eines Inserats.

    Art ist "ez", wenn der Text die Erstzulassung ausdruecklich benennt,
    sonst "modelljahr". Ein Modelljahr ist KEINE Erstzulassung: Fahrzeuge
    mit Modelljahr 2022 werden regelmaessig erst 2023 zugelassen.
    """
    if not text:
        return None, None, "unbekannt"
    m = _EZ_RE.search(text)
    if m:
        monat = int(m.group(1)) if m.group(1) else None
        if monat is not None and not 1 <= monat <= 12:
            monat = None
        return int(m.group(2)), monat, "ez"
    m = _MODELLJAHR_RE.search(text)
    if m:
        return int(m.group(1)), None, "modelljahr"
    return None, None, "unbekannt"


# Leistungsangaben: "110 kW (150 PS)" bevorzugt, sonst einzeln.
_POWER_KW_PS_RE = re.compile(r"(\d{2,3})\s*kw\s*[(/]\s*(\d{2,3})\s*ps", re.I)
_POWER_PS_RE = re.compile(r"(\d{2,3})\s*ps\b", re.I)
_POWER_KW_RE = re.compile(r"(\d{2,3})\s*kw\b", re.I)


def extract_power_ps(text: str | None) -> Optional[int]:
    """Liest die Motorleistung in PS aus einem Inseratstext.

    Portale schreiben meist "110 kW (150 PS)". Fehlt die PS-Angabe, wird
    aus kW umgerechnet. Werte ausserhalb eines plausiblen PKW-Bereichs
    werden verworfen.
    """
    if not text:
        return None
    m = _POWER_KW_PS_RE.search(text)
    if m:
        ps = int(m.group(2))
        return ps if 20 <= ps <= 1500 else None
    m = _POWER_PS_RE.search(text)
    if m:
        ps = int(m.group(1))
        return ps if 20 <= ps <= 1500 else None
    m = _POWER_KW_RE.search(text)
    if m:
        ps = round(int(m.group(1)) * PS_PER_KW)
        return ps if 20 <= ps <= 1500 else None
    return None


def infer_listing_details(listing: "Listing", query_zip: Optional[str] = None) -> None:
    """Extrahiert Akku/WLTP-Reichweite, Garantie, Standort-PLZ, Stadt und Distanz."""
    infer_listing_battery(listing)
    infer_listing_range(listing)

    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    if listing.warranty is None:
        listing.warranty = extract_warranty(text)

    # Zulassung: Monat und Art festhalten, damit ein Modelljahr nicht
    # stillschweigend als Erstzulassung gilt.
    if listing.power_ps in (None, 0):
        listing.power_ps = extract_power_ps(text)

    jahr, monat, art = extract_first_registration(text)
    if art != "unbekannt":
        if listing.first_registration_month is None and monat is not None:
            listing.first_registration_month = monat
        if listing.year_kind == "unbekannt":
            listing.year_kind = art
        if listing.year is None and jahr is not None:
            listing.year = jahr
    elif listing.year is not None and listing.year_kind == "unbekannt":
        # Portalwert ohne erkennbaren Beleg im Text.
        listing.year_kind = "portal"

    if listing.location:
        try:
            from kfz_crawler.geo import parse_location, calculate_distance_km
            zip_code, city = parse_location(listing.location)
            if zip_code:
                listing.location_zip = zip_code
            if city:
                listing.location_city = city
            if query_zip and listing.location_zip:
                listing.distance_km = calculate_distance_km(query_zip, listing.location_zip)
        except Exception:
            pass


@dataclass
class Listing:
    """Ein einzelnes Fahrzeug-Inserat, portal-übergreifend normalisiert."""

    portal: str
    title: str
    url: str
    price: Optional[int] = None          # in Euro
    year: Optional[int] = None           # Erstzulassung (Jahr)
    # Woher die Jahreszahl stammt: 'ez' (Erstzulassung), 'modelljahr',
    # 'titel' (blosse Jahreszahl) oder 'unbekannt'. Ein Modelljahr darf
    # die Erstzulassung nicht ersetzen – es liegt regelmaessig davor.
    year_kind: str = "unbekannt"
    first_registration_month: Optional[int] = None
    mileage: Optional[int] = None        # in km
    fuel: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None        # DE, AT, CH, FR, etc.
    raw_id: Optional[str] = None         # portal-eigene ID, wenn vorhanden

    # Erweiterte Attribute (wo das Portal sie liefert; sonst None):
    transmission: Optional[str] = None   # "schaltgetriebe" | "automatik"
    power_ps: Optional[int] = None       # Leistung in PS
    body: Optional[str] = None           # Beschreibungstext des Inserats
    body_type: Optional[str] = None      # Karosserieform (normalisiert)
    ev_range_km: Optional[int] = None     # elektrische Reichweite (km)
    # Nach welchem Standard die Reichweite gemessen wurde. Ein NEFZ-Wert
    # liegt deutlich ueber dem WLTP-Wert desselben Autos; ohne Standard
    # vergleicht ein Filter Ungleiches.
    ev_range_standard: str = "unbekannt"
    battery_kwh: Optional[float] = None   # tatsächlich im Inserat gelesener Wert
    # Welche Größe battery_kwh bezeichnet: "netto", "brutto" oder "unbekannt".
    # Ohne diese Angabe lässt sich der gelesene Wert nicht sicher mit einer
    # Filterschwelle vergleichen – netto und brutto liegen typisch 5 kWh auseinander.
    battery_observed_kind: str = "unbekannt"
    battery_net_kwh: Optional[float] = None
    battery_gross_kwh: Optional[float] = None
    battery_soh: Optional[float] = None   # Batteriezustand / State of Health (%)
    # Wie gut der SoH belegt ist: 'bestaetigt' (Zertifikat/Messung),
    # 'belegt' (ausdrueckliche SoH-Angabe), 'kandidat' (Prozentwert mit
    # Qualitaetsurteil ohne Batteriekontext) oder 'unbekannt'.
    battery_soh_level: str = "unbekannt"
    warranty: Optional[str] = None       # Garantie (z. B. "8 Jahre / 160.000 km", "12 Monate")
    location_zip: Optional[str] = None   # Postleitzahl des Standorts
    location_city: Optional[str] = None  # Stadt des Standorts
    distance_km: Optional[int] = None    # Entfernung in km zum Suchstandort
    image_urls: list[str] = field(default_factory=list, compare=False)
    field_evidence: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False)
    quality_score: Optional[float] = field(default=None, compare=False)
    unknown_fields: list[str] = field(default_factory=list, compare=False)
    is_stale: bool = field(default=False, compare=False)
    stale_since: Optional[float] = field(default=None, compare=False)
    detector_version: str = field(default=DETECTOR_VERSION, compare=False)

    # Wird vom DealFinder gefüllt:
    market_price: Optional[int] = field(default=None, compare=False)
    discount: Optional[float] = field(default=None, compare=False)  # 0.20 = 20 % unter Markt
    suspicious_reasons: list = field(default_factory=list, compare=False)
    is_deal: bool = field(default=False, compare=False)
    is_suspicious: bool = field(default=False, compare=False)

    @property
    def dedupe_key(self):
        """Grober Fahrzeug-Fingerabdruck für portalübergreifende Dubletten:
        gleiches Baujahr + exakter Kilometerstand identifizieren i. d. R. dasselbe
        Auto (auch wenn es von mehreren Händlern/Portalen inseriert wird)."""
        if self.year and self.mileage and self.mileage > 0:
            return (self.year, self.mileage)
        return None

    @property
    def fingerprint(self) -> str:
        """Stabiler Fingerabdruck zur Duplikat-Erkennung über Läufe hinweg."""
        base = self.raw_id or self.url
        return hashlib.sha1(f"{self.portal}:{base}".encode("utf-8")).hexdigest()

    def __str__(self) -> str:
        parts = [self.title]
        if self.price is not None:
            parts.append(f"{self.price:,} €".replace(",", "."))
        if self.year:
            parts.append(f"EZ {self.year}")
        if self.mileage is not None:
            parts.append(f"{self.mileage:,} km".replace(",", "."))
        if self.power_ps:
            parts.append(f"{self.power_ps} PS")
        if self.ev_range_km:
            parts.append(f"{self.ev_range_km} km Reichw.")
        return " | ".join(parts)


@dataclass
class SearchQuery:
    """Eine Nutzer-Suche aus der Konfiguration."""

    name: str
    make: str = ""
    model: str = ""
    exclude_makes: list = field(default_factory=list)  # Hersteller ausschließen
    exclude_models: list = field(default_factory=list)  # Modelle ausschließen
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    price_from: Optional[int] = None
    price_to: Optional[int] = None
    mileage_from: Optional[int] = None
    mileage_to: Optional[int] = None
    fuel: str = ""                       # benzin | diesel | elektro | hybrid | lpg | cng
    transmission: str = ""               # schaltgetriebe | automatik
    body_type: str = ""                  # limousine | kombi | suv | cabrio | coupe | van | kleinwagen
    power_from: Optional[int] = None     # Leistung ab … PS
    power_to: Optional[int] = None       # Leistung bis … PS
    seller: str = ""                     # haendler | privat
    doors: str = ""                      # "" | "2/3" | "4/5"
    # Standort & Umkreis:
    country: str = "DE"                  # DE | AT | CH | FR | NL | BE | IT | ES | PL | LU | ALL
    zip_code: str = ""                   # 5-stellige PLZ (z. B. "66111")
    radius_km: Optional[int] = None      # Umkreis in km (z. B. 50, 100, 200)
    # Zustand & Umwelt (Phase 2, server-seitig bei AutoScout24):
    emission_class: str = ""             # euro4 | euro5 | euro6 | euro6d | euro6e
    drivetrain: str = ""                 # allrad | front | heck
    include_damaged: bool = False        # Unfallwagen einschließen (Standard: nur unfallfrei)

    # E-Auto-spezifisch:
    ev_range_from: Optional[int] = None       # Mindest-Reichweite (km)
    battery_from_kwh: Optional[float] = None  # Mindest-Batteriekapazität (kWh)
    unknown_policy: str = "tolerant"          # tolerant | strict

    # Ausstattung: AutoScout24-IDs (server-seitig via eq=)
    equipment: list = field(default_factory=list)

    # Ausstattung / Freitext (Titel/Beschreibung):
    keywords: list = field(default_factory=list)        # müssen ALLE enthalten sein
    exclude_terms: list = field(default_factory=list)   # dürfen NICHT enthalten sein

    # Verwaltung (nur UI/DB):
    id: str = ""
    active: bool = True

    @staticmethod
    def _termlist(value) -> list:
        if value is None:
            return []
        if isinstance(value, str):
            parts = re.split(r"[,;\n]+", value)
        else:
            parts = list(value)
        return [str(p).strip() for p in parts if str(p).strip()]

    @staticmethod
    def _intlist(value) -> list:
        out = []
        for p in SearchQuery._termlist(value):
            try:
                out.append(int(p))
            except (TypeError, ValueError):
                continue
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "SearchQuery":
        def s(key: str) -> str:
            return str(d.get(key, "") or "").strip().lower()

        def raw_s(key: str) -> str:
            return str(d.get(key, "") or "").strip()

        def i(key: str):
            v = d.get(key)
            if v in (None, "", "null"):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        return cls(
            name=d.get("name", "Unbenannte Suche"),
            make=s("make"),
            model=s("model"),
            exclude_makes=cls._termlist(d.get("exclude_makes")),
            exclude_models=cls._termlist(d.get("exclude_models")),
            year_from=i("year_from"),
            year_to=i("year_to"),
            price_from=i("price_from"),
            price_to=i("price_to"),
            mileage_from=i("mileage_from"),
            mileage_to=i("mileage_to"),
            fuel=s("fuel"),
            transmission=s("transmission"),
            body_type=s("body_type"),
            power_from=i("power_from"),
            power_to=i("power_to"),
            seller=s("seller"),
            doors=s("doors"),
            country=s("country").upper() if s("country") else "DE",
            zip_code=raw_s("zip_code"),
            radius_km=i("radius_km"),
            emission_class=s("emission_class"),
            drivetrain=s("drivetrain"),
            include_damaged=bool(d.get("include_damaged", False)),
            ev_range_from=i("ev_range_from"),
            battery_from_kwh=(float(d["battery_from_kwh"])
                              if d.get("battery_from_kwh") not in (None, "", "null") else None),
            unknown_policy=s("unknown_policy") if s("unknown_policy") in ("tolerant", "strict") else "tolerant",
            equipment=cls._intlist(d.get("equipment")),
            keywords=cls._termlist(d.get("keywords")),
            exclude_terms=cls._termlist(d.get("exclude_terms")),
            id=str(d.get("id", "") or ""),
            active=bool(d.get("active", True)),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "active": self.active,
            "make": self.make, "model": self.model,
            "exclude_makes": self.exclude_makes,
            "exclude_models": self.exclude_models,
            "year_from": self.year_from, "year_to": self.year_to,
            "price_from": self.price_from, "price_to": self.price_to,
            "mileage_from": self.mileage_from, "mileage_to": self.mileage_to,
            "fuel": self.fuel, "transmission": self.transmission,
            "body_type": self.body_type, "power_from": self.power_from,
            "power_to": self.power_to, "seller": self.seller, "doors": self.doors,
            "country": self.country, "zip_code": self.zip_code, "radius_km": self.radius_km,
            "emission_class": self.emission_class, "drivetrain": self.drivetrain,
            "include_damaged": self.include_damaged,
            "ev_range_from": self.ev_range_from, "battery_from_kwh": self.battery_from_kwh,
            "unknown_policy": self.unknown_policy,
            "equipment": self.equipment,
            "keywords": self.keywords, "exclude_terms": self.exclude_terms,
        }


# Gängige Marken-Synonyme/Abkürzungen für den Titel-Abgleich.
MAKE_SYNONYMS = {
    "volkswagen": ["volkswagen", "vw"],
    "mercedes-benz": ["mercedes", "mercedes-benz", "benz"],
    "mercedes": ["mercedes", "mercedes-benz", "benz"],
    "bmw": ["bmw"],
}


def _make_tokens(make: str) -> list:
    return MAKE_SYNONYMS.get(make, [make])


# Kleinstfahrzeuge / Nicht-PKW, die (v. a. bei fuel=elektro ohne Marke) in den
# Ergebnissen auftauchen, aber keine echten PKW sind. Titel-Treffer -> ausschließen.
import re as _re

_NON_PKW_PATTERNS = [
    _re.compile(p) for p in [
        r"\b\d{2}\s*km/?h\b",            # "45 km/h", "25 km/h" (Leichtfahrzeuge)
        r"leichtfahrzeug", r"leichtkraftfahrzeug", r"leicht-?kfz",
        r"mini-?elektro", r"mini-?e-?auto", r"elektro-?mini",
        r"kabinenroller", r"\bkabinen", r"\btrike", r"lastentrike", r"lastenrad",
        r"\broller\b", r"e-?roller", r"elektroroller", r"e-?scooter", r"\bscooter\b",
        r"\bmofa\b", r"\bmoped\b", r"mokick", r"\bquad\b", r"\batv\b",
        r"\bpedelec", r"e-?bike", r"seniorenmobil", r"elektromobil",
        r"\bl6e\b", r"\bl7e\b", r"golf-?cart", r"golfcart", r"gabelstapler",
        r"\brikscha", r"tuk-?tuk", r"selbstfahr",
    ]
]


_DEFECT_AND_RESTRICTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bdefekt",
        r"\bbesch[aä]dig",
        r"\bunfall(?!frei)",
        r"\bunfallwagen\b",
        r"\bunfallauto\b",
        r"\bunfallfahrzeug\b",
        r"\bmotorschad",
        r"\bgetriebeschad",
        r"\bmotor\s*defekt",
        r"\bakkuschad",
        r"\bbatteriedefekt",
        r"\bbatterieschad",
        r"\bhagelschad",
        r"\bhagelschlag",
        r"\bglasschad",
        r"\bblechschad",
        r"\bwasserschad",
        r"\bbrandschad",
        r"\bsturmschad",
        r"\bbastler",
        r"\bteiletr[aä]ger",
        r"\bersatzteilspender",
        r"\bzum\s+ausschlachten",
        r"\btotalschad",
        r"\bsalvage\b",
        r"\bl[aä]uft\s+nicht",
        r"\bspringt\s+nicht\s+an",
        r"\bohne\s+t[üu]v\b",
        r"\bnicht\s+fahrbereit",
        r"\bkarosserieschad",
        r"\bkompressionsverlust\b",
        # Reine Export/Import/Gewerbe-Klauseln:
        r"\b(?:nur\s+(?:an|für)\s+)?export\b",
        r"\b(?:nur\s+(?:an|für)\s+)?import\b",
        r"\bimport(?:fahrzeug|wagen|auto)?\b",
        r"\b(?:nur\s+(?:an|für)\s+)?gewerbe(?:kunden|treibende)?\b",
        r"\b(?:nur\s+(?:an|für)\s+)?h[äa]ndler\b",
        r"\bkein\s+verkauf\s+an\s+privat\b",
        r"\breine(?:r)?\s+gewerbeverkauf\b",
    ]
]


def is_defective_or_restricted(listing: "Listing") -> bool:
    hay = f"{listing.title} {listing.body or ''}".lower()
    return any(p.search(hay) for p in _DEFECT_AND_RESTRICTION_PATTERNS)


def is_non_pkw(listing: "Listing") -> bool:
    hay = f"{listing.title} {listing.body or ''}".lower()
    return any(p.search(hay) for p in _NON_PKW_PATTERNS)


def battery_for_filter(l: "Listing") -> Optional[float]:
    """Vergleichswert für die Akkuschwelle. Bezugsgröße ist brutto.

    Ein reiner Nettowert wird NICHT gegen eine Brutto-Schwelle geprüft: die
    Bruttokapazität liegt höher (typisch +5 kWh), ein Vergleich würde also
    passende Fahrzeuge ausschließen. Solche Inserate gelten stattdessen als
    unbekannt und landen in der Stufe "plausibel".
    """
    if l.battery_gross_kwh is not None:
        return l.battery_gross_kwh
    kind = getattr(l, "battery_observed_kind", "unbekannt")
    if l.battery_kwh is not None and kind != "netto":
        return l.battery_kwh
    return None


@dataclass(frozen=True)
class FilterDecision:
    passed: bool
    reasons: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()


def evaluate_query(l: Listing, q: SearchQuery) -> FilterDecision:
    """Bewertet einen Treffer inklusive nachvollziehbarer Ausschlussgründe.

    Im Standardmodus bleiben unbekannte Werte erhalten. Der strikte Modus kann
    für Nutzer aktiviert werden, die nur vollständig belegte Datensätze sehen
    möchten.
    """
    reasons: list[str] = []
    unknown: list[str] = []
    title = (l.title or "").lower()
    hay = f"{l.title or ''} {l.body or ''}".lower()

    if is_non_pkw(l):
        reasons.append("kein PKW")
    if not q.include_damaged and is_defective_or_restricted(l):
        reasons.append("defekt, beschädigt oder Verkaufsbeschränkung")
    if q.make and title and not any(tok in title for tok in _make_tokens(q.make)):
        reasons.append("Hersteller passt nicht")
    if q.model and title and q.model not in title:
        reasons.append("Modell passt nicht")
    if q.exclude_makes and title and any(
        token in title for make in q.exclude_makes for token in _make_tokens(make.lower())
    ):
        reasons.append("Hersteller ausgeschlossen")
    if q.exclude_models and title and any(model.lower() in title for model in q.exclude_models):
        reasons.append("Modell ausgeschlossen")

    numeric_checks = (
        ("price", l.price, q.price_from, q.price_to, "Preis"),
        ("year", l.year, q.year_from, q.year_to, "Baujahr"),
        ("mileage", l.mileage, q.mileage_from, q.mileage_to, "Kilometerstand"),
        ("power_ps", l.power_ps, q.power_from, q.power_to, "Leistung"),
        ("ev_range_km", l.ev_range_km, q.ev_range_from, None, "Reichweite"),
        ("battery_kwh", battery_for_filter(l), q.battery_from_kwh, None, "Akkukapazität"),
    )
    for field_name, value, minimum, maximum, label in numeric_checks:
        if minimum is None and maximum is None:
            continue
        if value is None:
            unknown.append(field_name)
            continue
        if minimum is not None and value < minimum:
            reasons.append(f"{label} unter Mindestwert")
        if maximum is not None and value > maximum:
            reasons.append(f"{label} über Höchstwert")

    if q.fuel:
        if l.fuel:
            if q.fuel not in l.fuel.strip().lower():
                reasons.append("Kraftstoffart passt nicht")
        else:
            unknown.append("fuel")
    if q.transmission:
        if l.transmission:
            if q.transmission not in l.transmission.strip().lower():
                reasons.append("Getriebe passt nicht")
        else:
            unknown.append("transmission")
    if q.country and q.country.upper() != "ALL":
        if l.country:
            if l.country.upper() != q.country.upper():
                reasons.append("Land passt nicht")
        else:
            unknown.append("country")
    for term in q.keywords or []:
        if term.lower() not in hay:
            reasons.append(f"Stichwort fehlt: {term}")
    for term in q.exclude_terms or []:
        if term.lower() in hay:
            reasons.append(f"Ausschlusswort gefunden: {term}")

    unknown = list(dict.fromkeys(unknown))
    if q.unknown_policy == "strict" and unknown:
        reasons.extend(f"{field_name} unbekannt" for field_name in unknown)
    l.unknown_fields = unknown
    return FilterDecision(not reasons, tuple(reasons), tuple(unknown))


def matches_query(l: Listing, q: SearchQuery) -> bool:
    """Rückwärtskompatibler boolescher Nachfilter."""
    return evaluate_query(l, q).passed
