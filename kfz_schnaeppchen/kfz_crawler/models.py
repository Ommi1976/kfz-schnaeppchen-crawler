"""Gemeinsame Datenmodelle für alle Portale."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """Ein einzelnes Fahrzeug-Inserat, portal-übergreifend normalisiert."""

    portal: str
    title: str
    url: str
    price: Optional[int] = None          # in Euro
    year: Optional[int] = None           # Erstzulassung (Jahr)
    mileage: Optional[int] = None        # in km
    fuel: Optional[str] = None
    location: Optional[str] = None
    raw_id: Optional[str] = None         # portal-eigene ID, wenn vorhanden

    # Erweiterte Attribute (wo das Portal sie liefert; sonst None):
    transmission: Optional[str] = None   # "schaltgetriebe" | "automatik"
    power_ps: Optional[int] = None       # Leistung in PS
    body: Optional[str] = None           # Karosserieform (Freitext/normalisiert)
    ev_range_km: Optional[int] = None     # elektrische Reichweite (km)
    battery_kwh: Optional[float] = None   # Batteriekapazität (kWh)

    # Wird vom DealFinder gefüllt:
    market_price: Optional[int] = field(default=None, compare=False)
    discount: Optional[float] = field(default=None, compare=False)  # 0.20 = 20 % unter Markt
    suspicious_reasons: list = field(default_factory=list, compare=False)

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

    # E-Auto-spezifisch:
    ev_range_from: Optional[int] = None       # Mindest-Reichweite (km)
    battery_from_kwh: Optional[float] = None  # Mindest-Batteriekapazität (kWh)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchQuery":
        def s(key: str) -> str:
            return str(d.get(key, "") or "").strip().lower()

        return cls(
            name=d.get("name", "Unbenannte Suche"),
            make=s("make"),
            model=s("model"),
            year_from=d.get("year_from"),
            year_to=d.get("year_to"),
            price_from=d.get("price_from"),
            price_to=d.get("price_to"),
            mileage_from=d.get("mileage_from"),
            mileage_to=d.get("mileage_to"),
            fuel=s("fuel"),
            transmission=s("transmission"),
            body_type=s("body_type"),
            power_from=d.get("power_from"),
            power_to=d.get("power_to"),
            seller=s("seller"),
            doors=s("doors"),
            ev_range_from=d.get("ev_range_from"),
            battery_from_kwh=d.get("battery_from_kwh"),
        )


# Gängige Marken-Synonyme/Abkürzungen für den Titel-Abgleich.
MAKE_SYNONYMS = {
    "volkswagen": ["volkswagen", "vw"],
    "mercedes-benz": ["mercedes", "mercedes-benz", "benz"],
    "mercedes": ["mercedes", "mercedes-benz", "benz"],
    "bmw": ["bmw"],
}


def _make_tokens(make: str) -> list:
    return MAKE_SYNONYMS.get(make, [make])


def matches_query(l: Listing, q: SearchQuery) -> bool:
    """Clientseitiger Nachfilter.

    Grundsatz: Ein Kriterium schließt ein Inserat nur aus, wenn der Wert
    bekannt ist UND ihn verletzt. Fehlt der Wert im Inserat, wird NICHT
    ausgeschlossen (sonst würden brauchbare Treffer verloren gehen).
    """
    # Marke/Modell nur prüfen, wenn ein Titel vorliegt (Portale, die nicht
    # server-seitig nach Marke filtern, liefern gemischte Ergebnisse).
    title = (l.title or "").lower()
    if q.make and title:
        if not any(tok in title for tok in _make_tokens(q.make)):
            return False
    if q.model and title:
        if q.model not in title:
            return False
    if q.price_from and l.price is not None and l.price < q.price_from:
        return False
    if q.price_to and l.price is not None and l.price > q.price_to:
        return False
    if q.year_from and l.year is not None and l.year < q.year_from:
        return False
    if q.year_to and l.year is not None and l.year > q.year_to:
        return False
    if q.mileage_from and l.mileage is not None and l.mileage < q.mileage_from:
        return False
    if q.mileage_to and l.mileage is not None and l.mileage > q.mileage_to:
        return False
    if q.power_from and l.power_ps is not None and l.power_ps < q.power_from:
        return False
    if q.power_to and l.power_ps is not None and l.power_ps > q.power_to:
        return False
    if q.fuel and l.fuel:
        if q.fuel not in l.fuel.strip().lower():
            return False
    if q.transmission and l.transmission:
        if q.transmission not in l.transmission.strip().lower():
            return False
    if q.body_type:
        hay = f"{l.body or ''} {l.title}".lower()
        if q.body_type not in hay:
            return False
    # E-Auto-Filter
    if q.ev_range_from and l.ev_range_km is not None and l.ev_range_km < q.ev_range_from:
        return False
    if q.battery_from_kwh and l.battery_kwh is not None and l.battery_kwh < q.battery_from_kwh:
        return False
    return True
