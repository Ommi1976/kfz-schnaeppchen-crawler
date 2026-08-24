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
    (r"\bexport(fahrzeug)?\b", "Exportfahrzeug"),
    (r"\btotalschad", "Totalschaden"),
    (r"\bsalvage\b", "Salvage/US-Titel"),
    (r"\bl[aä]uft\s+nicht", "läuft nicht"),
    (r"\bspringt\s+nicht\s+an", "springt nicht an"),
    (r"\bohne\s+t[üu]v\b", "ohne TÜV"),
    (r"\bnicht\s+fahrbereit", "nicht fahrbereit"),
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
                coeffs = _fit_loglinear(kept) or coeffs

    return PriceModel(coeffs=coeffs, median=median, current_year=current_year)


@dataclass
class DealResult:
    deals: List[Listing]          # echte Schnäppchen
    suspicious: List[Listing]     # verdächtig (nicht melden, nur zählen)
    market_median: Optional[int]
    used_regression: bool


def find_deals(
    listings: List[Listing],
    deal_threshold: float,
    min_comparables: int,
    suspicious_discount: float = 0.6,
) -> DealResult:
    priced = [l for l in listings if l.price and l.price > 0]
    if len(priced) < min_comparables:
        return DealResult(deals=[], suspicious=[], market_median=None, used_regression=False)

    model = build_price_model(priced)
    if model is None:
        return DealResult(deals=[], suspicious=[], market_median=None, used_regression=False)

    deals: List[Listing] = []
    suspicious: List[Listing] = []
    for l in priced:
        exp = model.expected(l)
        if exp <= 0:
            continue
        discount = (exp - l.price) / exp
        l.market_price = exp
        l.discount = discount
        if discount < deal_threshold:
            continue
        reasons = fraud_reasons(l)
        if discount >= suspicious_discount and not reasons:
            reasons = ["unrealistisch günstig"]
        if reasons:
            l.suspicious_reasons = reasons
            suspicious.append(l)
        else:
            deals.append(l)

    deals.sort(key=lambda x: x.discount or 0, reverse=True)
    suspicious.sort(key=lambda x: x.discount or 0, reverse=True)
    return DealResult(
        deals=deals,
        suspicious=suspicious,
        market_median=model.median,
        used_regression=model.coeffs is not None,
    )


# --- Rückwärtskompatibilität ---------------------------------------------
def annotate_deals(
    listings: List[Listing],
    deal_threshold: float,
    min_comparables: int,
) -> List[Listing]:
    """Alte Signatur: liefert nur die (unverdächtigen) Schnäppchen."""
    return find_deals(listings, deal_threshold, min_comparables).deals
