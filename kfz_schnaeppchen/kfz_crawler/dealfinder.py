"""Schnäppchen-Erkennung.

Statt eines globalen Medians wird ein **erwarteter Preis** je Fahrzeug aus
Alter und Kilometerstand geschätzt (robuste log-lineare Regression). Ein
Inserat ist ein Schnäppchen, wenn sein Preis deutlich unter dem für *dieses*
Alter/diese Laufleistung erwarteten Preis liegt.

Zusätzlich werden verdächtige „zu-gut-um-wahr"-Inserate erkannt (Export,
Bastler, Motorschaden, Unfall …) und getrennt behandelt.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .models import Listing

# --- #5: Muster, die auf kein echtes Schnäppchen hindeuten ----------------
# \b Wortgrenzen; unfall NICHT wenn "unfallfrei".
_SUSPECT_PATTERNS: List[Tuple[str, str]] = [
    (r"\bunfall(?!frei)", "Unfall erwähnt"),
    (r"\bmotorschad", "Motorschaden"),
    (r"\bgetriebeschad", "Getriebeschaden"),
    (r"\bmotor\s*defekt", "Motor defekt"),
    (r"\bbastler", "Bastlerfahrzeug"),
    (r"\bteiletr[aä]ger", "Teileträger"),
    (r"\bersatzteilspender", "Ersatzteilspender"),
    (r"\bzum\s+ausschlachten", "zum Ausschlachten"),
    # Die einschraenkende Wendung muss dastehen. Mit optionalem Praefix traf
    # das Muster jede blosse Erwaehnung: "netto/Export moeglich" und
    # "Wartung beim Hyundai Haendler" beschreiben Autos, die verkauft werden.
    # Am Bestand gemessen trugen sieben Fahrzeuge zu Unrecht "Nur an Haendler".
    (r"\bexportfahrzeug\b", "Exportfahrzeug"),
    (r"\bnur\s+(?:an|f[üu]r)\s+(?:\S+\s+){0,3}export\b", "Exportfahrzeug"),
    (r"\btotalschad", "Totalschaden"),
    (r"\bsalvage\b", "Salvage/US-Titel"),
    (r"\bl[aä]uft\s+nicht", "läuft nicht"),
    (r"\bspringt\s+nicht\s+an", "springt nicht an"),
    (r"\bohne\s+t[üu]v\b", "ohne TÜV"),
    (r"\bnicht\s+fahrbereit", "nicht fahrbereit"),
    # Kein vollständiges Fahrzeug: der Preis bezieht sich auf Teile, nicht aufs Auto.
    (r"\brohkarosse", "Rohkarosse (kein fahrbares Fahrzeug)"),
    # "Karosserie" allein greift zu weit: "neue Karosserie" beschreibt eine
    # Instandsetzung, nicht ein Fahrzeug, das nur aus einer Karosse besteht.
    (r"\bnur\s+(?:die\s+)?karosse(?:rie)?\b", "nur Karosserie"),
    (r"\bkarosse(?:rie)?\s+ohne\b", "Karosserie ohne Anbauteile"),
    (r"\bohne\s+(?:motor|antrieb|getriebe)\b", "ohne Antrieb"),
    (r"\bohne\s+(?:akku|batterie)\b", "ohne Akku"),
    (r"\bakku\s+(?:defekt|fehlt)", "Akku defekt/fehlt"),
    (r"\bbrandschaden", "Brandschaden"),
    (r"\bwasserschaden", "Wasserschaden"),
    # Leasing: der genannte Betrag ist die Monatsrate, nicht der Kaufpreis.
    # Ohne diese Erkennung erscheint eine 78-€-Rate als 90-%-Schnäppchen.
    # "Leasingübnahme" (ohne "er") kommt in Inseraten häufig vor – Tippfehler
    # dürfen die Erkennung nicht aushebeln.
    (r"leasing\s*[üu]e?b(?:er)?nahme", "Leasingübernahme (Preis = Monatsrate)"),
    (r"\b[üu]e?bernahme\s+(?:des\s+)?(?:leasing|vertrag)", "Leasingübernahme (Preis = Monatsrate)"),
    (r"\brestleasing", "Restleasing (Preis = Monatsrate)"),
    (r"\blangzeitmiete", "Langzeitmiete (Preis = Monatsrate)"),
    (r"\bauto\s*abo\b", "Auto-Abo (Preis = Monatsrate)"),
    # Das allgemeine Muster tritt hinter die genaueren zurueck.
    (r"\bleasing(?!\s*[üu]e?b)", "Leasing (Preis = Monatsrate)"),
    (r"\bmtl\.?\b", "Monatsrate/Leasing"),
    (r"\bmonatlich(?:e[rn]?)?\s+rate", "Monatsrate/Leasing"),
    (r"\b\d+\s*€\s*(?:brutto\s+)?monatl", "Monatsrate/Leasing"),
    (r"\brestlaufzeit", "Leasing-Restlaufzeit"),
    # Lockangebote / Neuwagen-Anzahlungen (kein echtes Gebrauchtwagen-Schnäppchen)
    (r"sofort\s+verf[üu]gbar", "Lockangebot (sofort verfügbar)"),
    (r"sofort\s+lieferbar", "Lockangebot (sofort lieferbar)"),
    (r"\banzahlung\b", "Anzahlung/Leasing"),
    (r"\bmonatsrate\b", "Monatsrate/Leasing"),
    (r"\bpro\s+monat\b", "Monatsrate/Leasing"),
    (r"\b(?:ab|nur)\s+\d+\s*€\s*/\s*m(?:onat)?\b", "Monatsrate/Abo"),
    (r"\bnur\s+(?:an|f[üu]r)\s+(?:\S+\s+){0,3}gewerb", "Nur an Gewerbe"),
    (r"\bgewerblicher\s+(?:ver)?kauf\b", "Nur an Gewerbe"),
    (r"\bnur\s+(?:an|f[üu]r)\s+(?:\S+\s+){0,3}h[äa]ndler\b", "Nur an Händler"),
    (r"\bh[äa]ndleranfragen?\s+(?:erw[üu]nscht|willkommen)", "Nur an Händler"),
    (r"\b[üu]berf[üu]hrungskosten\b", "Überführungskosten (Neuwagen)"),
    (r"\bwerksabholung\b", "Werksabholung (Neuwagen)"),
    (r"\bhaushalt(?:skunde|spreis)\b", "Lockangebot/Haushaltspreis"),
    (r"\bbestellfahrzeug", "Bestellfahrzeug"),
    (r"\bkonfigurier", "Konfigurierbar (Neuwagen)"),
    (r"lieferzeit", "Lieferzeit (Neuwagen)"),
    (r"\bverf[üu]gbar\s+ab\b", "verfügbar ab (Neuwagen)"),
    (r"probefahrt\s+m[öo]glich.*neu", "Neuwagen"),
]


def fraud_reasons(listing: Listing) -> List[str]:
    """Gibt eine Liste von Verdachtsgründen zurück (leer = unauffällig)."""
    hay = f"{listing.title} {listing.body or ''}".lower()
    reasons = []
    for pattern, label in _SUSPECT_PATTERNS:
        if re.search(pattern, hay):
            reasons.append(label)
    return reasons


# --- #3: Preis-Schätzung --------------------------------------------------
def _median_price(listings: List[Listing]) -> Optional[int]:
    prices = [l.price for l in listings if l.price and l.price > 0]
    if not prices:
        return None
    return int(statistics.median(prices))


def _solve_3x3(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Löst A·x = b (3×3) per Cramer'scher Regel. None bei Singularität."""
    def det3(m):
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    d = det3(A)
    if abs(d) < 1e-9:
        return None
    x = []
    for i in range(3):
        Ai = [row[:] for row in A]
        for r in range(3):
            Ai[r][i] = b[r]
        x.append(det3(Ai) / d)
    return x


def _fit_loglinear(points: List[Tuple[float, float, float]]) -> Optional[Tuple[float, float, float]]:
    """OLS-Fit von ln(preis) = b0 + b1·alter + b2·(km/10000).

    points: Liste von (alter_jahre, km, preis). Rückgabe (b0, b1, b2) oder None.
    """
    if len(points) < 6:
        return None
    ages = [p[0] for p in points]
    kms = [p[1] / 10000.0 for p in points]
    ys = [math.log(p[2]) for p in points]

    # Genug Varianz in den Prädiktoren nötig, sonst ist die Steigung Unsinn.
    if (max(ages) - min(ages) < 1) and (max(kms) - min(kms) < 1):
        return None

    n = len(points)
    sa, sk = sum(ages), sum(kms)
    saa = sum(a * a for a in ages)
    skk = sum(k * k for k in kms)
    sak = sum(a * k for a, k in zip(ages, kms))
    sy = sum(ys)
    say = sum(a * y for a, y in zip(ages, ys))
    sky = sum(k * y for k, y in zip(kms, ys))

    A = [
        [n,  sa,  sk],
        [sa, saa, sak],
        [sk, sak, skk],
    ]
    b = [sy, say, sky]
    return _solve_3x3(A, b)


@dataclass
class PriceModel:
    """Erwarteten Preis je Fahrzeug liefern; fällt bei Bedarf auf Median zurück."""
    coeffs: Optional[Tuple[float, float, float]]
    median: int
    current_year: int

    def expected(self, l: Listing) -> int:
        if self.coeffs and l.year and l.mileage is not None:
            b0, b1, b2 = self.coeffs
            age = max(0, self.current_year - l.year)
            pred = math.exp(b0 + b1 * age + b2 * (l.mileage / 10000.0))
            if pred and math.isfinite(pred) and pred > 0:
                # Auf plausiblen Bereich um den Median begrenzen.
                return int(min(max(pred, self.median * 0.25), self.median * 4))
        return self.median


def build_price_model(listings: List[Listing]) -> Optional[PriceModel]:
    priced = [l for l in listings if l.price and l.price > 0]
    median = _median_price(priced)
    if median is None:
        return None
    current_year = datetime.now().year

    pts = [
        (float(current_year - l.year), float(l.mileage), float(l.price))
        for l in priced
        if l.year and l.mileage is not None and l.price
    ]
    coeffs = _fit_loglinear(pts)

    # Robustheit: Ausreißer (großer Residuenbetrag) einmal entfernen und neu fitten.
    if coeffs and len(pts) >= 8:
        b0, b1, b2 = coeffs
        resids = [
            math.log(p[2]) - (b0 + b1 * p[0] + b2 * (p[1] / 10000.0)) for p in pts
        ]
        sd = statistics.pstdev(resids) or 0.0
        if sd > 0:
            kept = [p for p, r in zip(pts, resids) if abs(r) <= 2.5 * sd]
            if len(kept) >= 6:
                # Nach dem Trimmen neu fitten. Schlägt der Fit fehl (zu wenig
                # Varianz), auf Median zurückfallen statt die verzerrte
                # ursprüngliche Schätzung zu behalten.
                coeffs = _fit_loglinear(kept)

    return PriceModel(coeffs=coeffs, median=median, current_year=current_year)


@dataclass
class DealResult:
    deals: List[Listing]          # echte Schnäppchen (is_deal)
    suspicious: List[Listing]     # verdächtig / Lockangebote (is_suspicious)
    priced: List[Listing]         # ALLE bepreisten Inserate (annotiert)
    market_median: Optional[int]
    used_regression: bool


# Preise, die kein Angebot sind, sondern ein Platzhalter beim Einstellen.
_PLATZHALTER_PREISE = {1111, 1234, 11111, 12345, 22222, 33333, 99999, 111111, 123456}


def _ist_platzhalterpreis(preis: Optional[int]) -> bool:
    """Erkennt Tippmuster wie 12.345 € oder 11.111 €.

    Solche Betraege stehen vor allem bei Neuwagen, wenn der Haendler den Preis
    noch nicht gepflegt hat. Als Schnaeppchen gerechnet ergeben sie absurde
    Rabatte.
    """
    return bool(preis) and preis in _PLATZHALTER_PREISE


def _classify(l: Listing, discount: float, deal_threshold: float,
              suspicious_discount: float) -> None:
    """Setzt is_deal / is_suspicious / suspicious_reasons auf dem Inserat."""
    reasons = fraud_reasons(l)
    if _ist_platzhalterpreis(l.price):
        reasons = reasons + [f"Platzhalterpreis ({l.price} €)"]
    # Lockangebot: Neuwagen (0 km) deutlich unter Markt = Anzahlung/Köder.
    if (l.mileage == 0) and discount >= deal_threshold and "0 km" not in " ".join(reasons):
        reasons = reasons + ["Lockangebot (Neuwagen, 0 km)"]
    if discount >= suspicious_discount and not reasons:
        reasons = ["unrealistisch günstig"]
    l.suspicious_reasons = reasons
    l.is_suspicious = bool(reasons)
    l.is_deal = (discount >= deal_threshold) and not l.is_suspicious


def find_deals(
    listings: List[Listing],
    deal_threshold: float,
    min_comparables: int,
    suspicious_discount: float = 0.6,
) -> DealResult:
    priced = [l for l in listings if l.price and l.price > 0]
    if len(priced) < min_comparables:
        return DealResult(deals=[], suspicious=[], priced=priced,
                          market_median=None, used_regression=False)

    # Bei E-Autos dürfen unterschiedliche Modelle und Akkuvarianten nicht in
    # ein gemeinsames Preisniveau fallen. Varianten werden zuerst, Modellfamilien
    # als vorsichtiger Fallback verwendet. Nicht-EV-Suchen behalten das globale
    # Modell für Rückwärtskompatibilität.
    ev_specs: dict[int, object] = {}
    try:
        from .ev_database import lookup_ev_spec
        for listing in priced:
            spec = lookup_ev_spec(listing.title, listing.body, power_ps=listing.power_ps)
            if spec:
                ev_specs[id(listing)] = spec
    except Exception:
        ev_specs = {}

    models: dict[int, PriceModel] = {}
    built_models: list[PriceModel] = []
    if ev_specs:
        variant_groups: dict[tuple, list[Listing]] = {}
        family_groups: dict[tuple, list[Listing]] = {}
        for listing in priced:
            spec = ev_specs.get(id(listing))
            if not spec:
                continue
            variant_groups.setdefault((spec.make, spec.model, spec.variant), []).append(listing)
            family_groups.setdefault((spec.make, spec.model), []).append(listing)
        for listing in priced:
            spec = ev_specs.get(id(listing))
            if not spec:
                continue
            candidates = variant_groups[(spec.make, spec.model, spec.variant)]
            if len(candidates) < min_comparables:
                candidates = family_groups[(spec.make, spec.model)]
            if len(candidates) < min_comparables:
                continue
            model = build_price_model(candidates)
            if model:
                models[id(listing)] = model
                if model not in built_models:
                    built_models.append(model)
    else:
        model = build_price_model(priced)
        if model:
            built_models.append(model)
            models = {id(listing): model for listing in priced}

    if not models:
        return DealResult(deals=[], suspicious=[], priced=priced,
                          market_median=None, used_regression=False)

    deals: List[Listing] = []
    suspicious: List[Listing] = []
    for l in priced:
        model = models.get(id(l))
        if model is None:
            l.market_price = None
            l.discount = None
            l.is_deal = False
            continue
        exp = model.expected(l)
        if exp <= 0:
            continue
        discount = (exp - l.price) / exp
        l.market_price = exp
        l.discount = discount
        _classify(l, discount, deal_threshold, suspicious_discount)
        if l.is_deal:
            deals.append(l)
        elif l.is_suspicious:
            suspicious.append(l)

    deals.sort(key=lambda x: x.discount or 0, reverse=True)
    suspicious.sort(key=lambda x: x.discount or 0, reverse=True)
    priced.sort(key=lambda x: x.discount or -1, reverse=True)
    return DealResult(
        deals=deals,
        suspicious=suspicious,
        priced=priced,
        market_median=int(statistics.median(m.median for m in built_models)),
        used_regression=any(m.coeffs is not None for m in built_models),
    )


def _identity_tokens(title: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", (title or "").lower())
    normalized = re.sub(r"\bvw\b", "volkswagen", normalized)
    stop = {
        "navi", "klima", "automatik", "led", "pdc", "shz", "top", "neu",
        "elektro", "electric", "unfallfrei", "sofort", "verfügbar",
    }
    return {token for token in normalized.split() if len(token) > 1 and token not in stop and not token.isdigit()}


def _same_vehicle_identity(left: Listing, right: Listing) -> bool:
    try:
        from .ev_database import lookup_ev_spec
        lspec = lookup_ev_spec(left.title, left.body, power_ps=left.power_ps)
        rspec = lookup_ev_spec(right.title, right.body, power_ps=right.power_ps)
        if lspec and rspec:
            return (
                lspec.make == rspec.make
                and lspec.model == rspec.model
                and abs(lspec.battery_gross_kwh - rspec.battery_gross_kwh) <= 2.0
            )
    except Exception:
        pass
    lt, rt = _identity_tokens(left.title), _identity_tokens(right.title)
    if not lt or not rt:
        return False
    similarity = len(lt & rt) / len(lt | rt)
    image_overlap = bool(set(left.image_urls or []) & set(right.image_urls or []))
    return similarity >= 0.45 or image_overlap


def dedupe(listings: List[Listing]) -> List[Listing]:
    """Portalübergreifende Dubletten entfernen: gleiches Baujahr + exakter
    Kilometerstand = i. d. R. dasselbe Auto. Bei nahen Preisen wird das
    günstigere behalten; weichen die Preise stark ab, sind es vermutlich
    verschiedene Autos und beide bleiben erhalten."""
    kept: List[Listing] = []
    index: dict = {}
    for l in listings:
        k = l.dedupe_key
        if k is None:
            kept.append(l)
            continue
        if k not in index:
            index[k] = len(kept)
            kept.append(l)
            continue
        i = index[k]
        other = kept[i]
        if not _same_vehicle_identity(l, other):
            kept.append(l)
            continue
        lp, op = l.price or 0, other.price or 0
        if l.portal.lower() == "autouncle":
            # Bei gleicher erkannter Fahrzeugidentität hat die übergreifende
            # AutoUncle-Quelle immer Vorrang, unabhängig von Preisabweichungen.
            kept[i] = l
            continue
        if other.portal.lower() == "autouncle":
            continue
        close = lp and op and min(lp, op) >= 0.7 * max(lp, op)
        if close:
            if lp and (op == 0 or lp < op):
                kept[i] = l   # günstigeres Angebot desselben Autos behalten
        else:
            kept.append(l)    # deutlich anderer Preis -> vermutlich anderes Auto
    return kept


# --- Rückwärtskompatibilität ---------------------------------------------
def annotate_deals(
    listings: List[Listing],
    deal_threshold: float,
    min_comparables: int,
) -> List[Listing]:
    """Alte Signatur: liefert nur die (unverdächtigen) Schnäppchen."""
    return find_deals(listings, deal_threshold, min_comparables).deals
