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
    r"(?<!gesamt)(?:reichweite|range)\D{0,24}(\d{2,4})\s*km|"
    r"(\d{2,4})\s*km\D{0,24}(?<!gesamt)(?:reichweite|range)",
    re.IGNORECASE,
)
_BATTERY_SOH_RE = re.compile(
    r"\bsoh\s*[:=)}\]]?\s*(\d{2,3}(?:[.,]\d+)?)\s*%?|"
    r"\b(?:batterie|akku)(?:-?\s*status|gesundheit|zustand|kapazit[aä]t)\D{0,30}(\d{2,3}(?:[.,]\d+)?)\s*%|"
    r"\b(?:batterie-information|batterie-status)\D{0,40}(\d{2,3}(?:[.,]\d+)?)\s*%|"
    r"\bgesundheitszustand\s*\(?(?:soh)?\)?\s*[:=]?\s*(\d{2,3}(?:[.,]\d+)?)\s*%|"
    r"\b(\d{2,3}(?:[.,]\d+)?)\s*%\s*(?:sehr\s*gut|gut|ausgezeichnet)\b|"
    r"\bstate\s+of\s+health\s*[:=]?\s*(\d{2,3}(?:[.,]\d+)?)\s*%?",
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


def extract_battery_soh(text: str | None) -> Optional[float]:
    """Erkennt den State of Health (SoH in %) aus Texten, z. B. ``Batterie-Status 94.6%``, ``SOH 96%`` oder ``Batteriezustand 97%``."""
    if not text:
        return None
    for match in _BATTERY_SOH_RE.finditer(str(text)):
        for val in match.groups():
            if val:
                try:
                    num = float(val.replace(",", "."))
                    if 50.0 <= num <= 100.0:
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
    # Cupra Born
    (re.compile(r"\bborn\b.*?\b(?:77|e-boost\s*77)\b", re.I), 77.0, 548),
    (re.compile(r"\bborn\b.*?\b(?:58|150\s*kw|170\s*kw)\b", re.I), 58.0, 424),
    (re.compile(r"\bborn\b", re.I), 58.0, 424),
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
]


def infer_ev_specs_from_model(text: str | None) -> tuple[Optional[float], Optional[int]]:
    """Gibt (akku_kwh, reichweite_km) anhand bekannter E-Auto-Modellmuster zurück."""
    if not text:
        return None, None
    for pattern, kwh, rng in _KNOWN_EV_CATALOG:
        if pattern.search(text):
            return kwh, rng
    return None, None


def infer_listing_battery(listing: "Listing", check_images: bool = False) -> None:
    """Füllt den Akkuwert und SoH nach:
    1. Priorität (im Zweifel): Expliziter Wert aus dem Inseratstext/Titel.
    2. Priorität: Interne Referenzdatenbank (ev_database).
    """
    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    explicit_kwh = extract_battery_kwh(text)
    if explicit_kwh is not None:
        listing.battery_kwh = explicit_kwh
    elif listing.battery_kwh is None:
        try:
            from kfz_crawler.ev_database import lookup_ev_spec
            spec = lookup_ev_spec(listing.title, getattr(listing, "body", ""))
            if spec:
                listing.battery_kwh = spec.battery_gross_kwh
        except Exception:
            kwh, _ = infer_ev_specs_from_model(text)
            if kwh is not None:
                listing.battery_kwh = kwh

    if listing.battery_soh is None:
        listing.battery_soh = extract_battery_soh(text)
        if listing.battery_soh is None and check_images and getattr(listing, "image_urls", None):
            try:
                from kfz_crawler.battery_analyzer import extract_soh_from_image_urls
                listing.battery_soh = extract_soh_from_image_urls(listing.image_urls)
            except Exception:
                pass


def infer_listing_range(listing: "Listing") -> None:
    """Füllt die elektrische Reichweite nach:
    1. Priorität (im Zweifel): Expliziter Wert aus dem Inseratstext/Titel.
    2. Priorität: Interne Referenzdatenbank (ev_database).
    """
    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    explicit_rng = extract_ev_range_km(text)
    if explicit_rng is not None:
        listing.ev_range_km = explicit_rng
    elif listing.ev_range_km is None:
        try:
            from kfz_crawler.ev_database import lookup_ev_spec
            spec = lookup_ev_spec(listing.title, getattr(listing, "body", ""))
            if spec:
                listing.ev_range_km = spec.wltp_range_km
        except Exception:
            _, rng = infer_ev_specs_from_model(text)
            if rng is not None:
                listing.ev_range_km = rng


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
    battery_soh: Optional[float] = None   # Batteriezustand / State of Health (%)
    image_urls: list[str] = field(default_factory=list, compare=False)

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
    zip_code: str = ""                   # 5-stellige PLZ (z. B. "66111")
    radius_km: Optional[int] = None      # Umkreis in km (z. B. 50, 100, 200)
    # Zustand & Umwelt (Phase 2, server-seitig bei AutoScout24):
    emission_class: str = ""             # euro4 | euro5 | euro6 | euro6d | euro6e
    drivetrain: str = ""                 # allrad | front | heck
    include_damaged: bool = False        # Unfallwagen einschließen (Standard: nur unfallfrei)

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
            zip_code=raw_s("zip_code"),
            radius_km=i("radius_km"),
            emission_class=s("emission_class"),
            drivetrain=s("drivetrain"),
            include_damaged=bool(d.get("include_damaged", False)),
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
            "zip_code": self.zip_code, "radius_km": self.radius_km,
            "emission_class": self.emission_class, "drivetrain": self.drivetrain,
            "include_damaged": self.include_damaged,
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


_DEFECT_AND_RESTRICTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bdefekt\b",
        r"\bbesch[aä]digt\b",
        r"\bunfall(?!frei)",
        r"\bmotorschad",
        r"\bgetriebeschad",
        r"\bmotor\s*defekt",
        r"\bakkuschad",
        r"\bbatteriedefekt",
        r"\bbatterieschad",
        r"\bhagelschad",
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


def matches_query(l: Listing, q: SearchQuery) -> bool:
    """Clientseitiger Nachfilter.

    Grundsatz: Ein Kriterium schließt ein Inserat nur aus, wenn der Wert
    bekannt ist UND ihn verletzt. Fehlt der Wert im Inserat, wird NICHT
    ausgeschlossen (sonst würden brauchbare Treffer verloren gehen).
    """
    # Kleinstfahrzeuge / Nicht-PKW grundsätzlich ausschließen (echte PKW-Suche).
    if is_non_pkw(l):
        return False

    # Defekte / Schäden / reine Import-Export-Fahrzeuge grundsätzlich ausschließen
    # (außer include_damaged ist in der Suche explizit aktiviert).
    if not q.include_damaged and is_defective_or_restricted(l):
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
    if q.ev_range_from and q.battery_from_kwh:
        # Wenn BEIDES definiert ist, gilt ODER-Logik (z.B. Akku >= 65 kWh ODER Reichweite >= 450 km):
        # Nur ausschließen, wenn beide Werte bekannt sind und beide unter der Mindestanforderung liegen.
        if l.ev_range_km is not None and l.battery_kwh is not None:
            if l.ev_range_km < q.ev_range_from and l.battery_kwh < q.battery_from_kwh:
                return False
    elif q.ev_range_from and l.ev_range_km is not None and l.ev_range_km < q.ev_range_from:
        return False
    elif q.battery_from_kwh and l.battery_kwh is not None and l.battery_kwh < q.battery_from_kwh:
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
