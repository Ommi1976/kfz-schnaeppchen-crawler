"""Gemeinsame Datenmodelle für alle Portale."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional


_BATTERY_KWH_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*k\s*wh\b",
    re.IGNORECASE,
)
_EV_RANGE_RE = re.compile(
    r"(?:reichweite|range)\D{0,24}(\d{2,4})\s*km|"
    r"(\d{2,4})\s*km\D{0,24}(?:reichweite|range)",
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
        if 5 <= value <= 200:
            values.append(value)
    return max(values) if values else None


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


def infer_listing_battery(listing: "Listing") -> None:
    """Füllt den Akkuwert aus Titel/Text nach, falls das Portal ihn nicht liefert."""
    if listing.battery_kwh is not None:
        return
    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    listing.battery_kwh = extract_battery_kwh(text)


def infer_listing_range(listing: "Listing") -> None:
    """Füllt die elektrische Reichweite aus Titel/Text nach."""
    if listing.ev_range_km is not None:
        return
    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    listing.ev_range_km = extract_ev_range_km(text)


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

    # E-Auto-spezifisch:
    ev_range_from: Optional[int] = None       # Mindest-Reichweite (km)
    battery_from_kwh: Optional[float] = None  # Mindest-Batteriekapazität (kWh)

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
            ev_range_from=i("ev_range_from"),
            battery_from_kwh=(float(d["battery_from_kwh"])
                              if d.get("battery_from_kwh") not in (None, "", "null") else None),
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
            "ev_range_from": self.ev_range_from, "battery_from_kwh": self.battery_from_kwh,
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


def is_non_pkw(listing: "Listing") -> bool:
    hay = f"{listing.title} {listing.body or ''}".lower()
    return any(p.search(hay) for p in _NON_PKW_PATTERNS)


def matches_query(l: Listing, q: SearchQuery) -> bool:
    """Clientseitiger Nachfilter.

    Grundsatz: Ein Kriterium schließt ein Inserat nur aus, wenn der Wert
    bekannt ist UND ihn verletzt. Fehlt der Wert im Inserat, wird NICHT
    ausgeschlossen (sonst würden brauchbare Treffer verloren gehen).
    """
    # Kleinstfahrzeuge / Nicht-PKW grundsätzlich ausschließen (echte PKW-Suche).
    if is_non_pkw(l):
        return False

    # Marke/Modell nur prüfen, wenn ein Titel vorliegt (Portale, die nicht
    # server-seitig nach Marke filtern, liefern gemischte Ergebnisse).
    title = (l.title or "").lower()
    if q.make and title:
        if not any(tok in title for tok in _make_tokens(q.make)):
            return False
    if q.model and title:
        if q.model not in title:
            return False
    # Ausschlüsse werden bewusst erst nach dem Portalabruf geprüft, weil die
    # Portale dafür keine einheitliche serverseitige Schnittstelle haben.
    if q.exclude_makes and title:
        if any(
            token in title
            for make in q.exclude_makes
            for token in _make_tokens(make.lower())
        ):
            return False
    if q.exclude_models and title:
        if any(model.lower() in title for model in q.exclude_models):
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
    # Karosserie wird bei AutoScout24 server-seitig gefiltert (body=<id>); ein
    # Titel-Keyword-Abgleich würde korrekte Treffer fälschlich ausschließen
    # (z. B. „Golf Variant" ohne das Wort „Kombi"), daher hier kein Nachfilter.
    # E-Auto-Filter
    if q.ev_range_from and l.ev_range_km is not None and l.ev_range_km < q.ev_range_from:
        return False
    # Ein bekannter kleiner Akku wird immer ausgeschlossen. Fehlt die kWh-
    # Angabe, reicht bei gesetztem Reichweitenfilter eine nachweislich passende
    # Reichweite als sinnvolle Fallback-Prüfung.
    if q.battery_from_kwh:
        if l.battery_kwh is not None:
            if l.battery_kwh < q.battery_from_kwh:
                return False
        elif q.ev_range_from and l.ev_range_km is not None and l.ev_range_km >= q.ev_range_from:
            pass
        elif q.ev_range_from and l.portal == "AutoScout24":
            # AutoScout24 setzt erange server-seitig; falls die Reichweite in
            # der Ergebnisliste fehlt, ist dieser Treffer trotzdem verifiziert.
            pass
        else:
            return False

    # Ausstattung / Freitext: Stichwörter müssen ALLE vorkommen, Ausschluss keiner.
    hay = f"{l.title or ''} {l.body or ''}".lower()
    for term in getattr(q, "keywords", []) or []:
        if term.lower() not in hay:
            return False
    for term in getattr(q, "exclude_terms", []) or []:
        if term.lower() in hay:
            return False
    return True
