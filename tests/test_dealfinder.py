import math
from datetime import datetime
import pytest

from kfz_crawler.dealfinder import (
    PriceModel,
    build_price_model,
    dedupe,
    find_deals,
    fraud_reasons,
    _fit_loglinear,
    _solve_3x3,
)
from kfz_crawler.models import Listing


def test_fraud_reasons_unfall():
    l_clean = Listing(portal="AS24", title="VW Golf 7 TDI unfallfrei Top Zustand", url="http://x")
    assert fraud_reasons(l_clean) == []

    l_damaged = Listing(portal="AS24", title="VW Golf 7 TDI Unfallschaden vorne", url="http://x")
    reasons = fraud_reasons(l_damaged)
    assert any("Unfall" in r for r in reasons)


def test_fraud_reasons_engine_and_export():
    l1 = Listing(portal="KA", title="BMW 320d Motorschaden Bastler", url="http://x")
    r1 = fraud_reasons(l1)
    assert "Motorschaden" in r1
    assert "Bastlerfahrzeug" in r1

    l2 = Listing(portal="KA", title="Audi A4 nur für Export / Gewerbe", url="http://x")
    r2 = fraud_reasons(l2)
    assert "Exportfahrzeug" in r2
    assert "Nur an Gewerbe" in r2


def test_fraud_reasons_leasing_and_rates():
    l = Listing(portal="MOB", title="Golf 8 GTI ab 199 € / Monat Monatsrate", url="http://x")
    r = fraud_reasons(l)
    assert "Monatsrate/Leasing" in r or "Monatsrate/Abo" in r


def test_solve_3x3():
    # Identitätsmatrix: x = b
    A = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    b = [2.0, 3.0, 4.0]
    res = _solve_3x3(A, b)
    assert res == [2.0, 3.0, 4.0]

    # Singuläre Matrix
    A_sing = [
        [1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0],
        [0.0, 0.0, 1.0],
    ]
    assert _solve_3x3(A_sing, b) is None


def test_fit_loglinear_insufficient_points():
    pts = [(1.0, 10000.0, 20000.0), (2.0, 20000.0, 18000.0)]
    assert _fit_loglinear(pts) is None


def test_build_price_model_regression():
    curr = datetime.now().year
    # Synthetische Daten mit unabhängiger Varianz in Alter und Laufleistung
    test_points = [
        (1, 15000, 28000),
        (1, 40000, 24000),
        (2, 25000, 23500),
        (2, 70000, 19500),
        (3, 40000, 19000),
        (3, 90000, 15500),
        (4, 50000, 16000),
        (4, 110000, 13000),
        (5, 60000, 13500),
        (5, 140000, 10000),
    ]
    listings = [
        Listing(
            portal="AS24",
            title=f"Auto {i}",
            url=f"http://x/{i}",
            price=price,
            year=curr - age,
            mileage=km,
        )
        for i, (age, km, price) in enumerate(test_points)
    ]

    model = build_price_model(listings)
    assert model is not None
    assert model.coeffs is not None
    b0, b1, b2 = model.coeffs
    assert b1 < 0  # Preis sinkt mit dem Alter
    assert b2 < 0  # Preis sinkt mit km

    # Test expected price calculation
    test_car = Listing(portal="AS24", title="Test", url="http://x", year=curr - 3, mileage=40000)
    exp_price = model.expected(test_car)
    assert exp_price > 0
    assert abs(exp_price - model.median) < model.median * 2


def test_find_deals_and_suspicious():
    curr = datetime.now().year
    # Erstelle 10 normale Fahrzeuge um ~20.000 €
    listings = [
        Listing(portal="AS24", title=f"Car {i}", url=f"http://x/{i}",
                price=20000 + i * 200, year=curr - 3, mileage=50000 + i * 1000)
        for i in range(10)
    ]
    # Echtes Schnäppchen (25% unter Markt)
    deal_car = Listing(portal="AS24", title="Super Deal", url="http://x/deal",
                       price=14000, year=curr - 3, mileage=50000)
    # Zu billig / verdächtig (70% Rabatt)
    too_cheap = Listing(portal="AS24", title="Zu billig", url="http://x/cheap",
                        price=4000, year=curr - 3, mileage=50000)
    # Motorschaden
    broken_car = Listing(portal="AS24", title="Golf Motorschaden defekt", url="http://x/broken",
                         price=10000, year=curr - 3, mileage=50000)

    all_list = listings + [deal_car, too_cheap, broken_car]
    result = find_deals(all_list, deal_threshold=0.15, min_comparables=5, suspicious_discount=0.6)

    assert len(result.deals) >= 1
    assert any(d.url == "http://x/deal" for d in result.deals)
    assert any(s.url == "http://x/cheap" for s in result.suspicious)
    assert any(s.url == "http://x/broken" for s in result.suspicious)


def test_dedupe_same_car():
    l1 = Listing(portal="AS24", title="VW Golf", url="http://as24/1", price=15000, year=2019, mileage=85432)
    l2 = Listing(portal="KA", title="Volkswagen Golf", url="http://ka/1", price=14500, year=2019, mileage=85432)
    l3 = Listing(portal="MOB", title="VW Golf anderer", url="http://mob/1", price=15000, year=2019, mileage=90000)

    deduped = dedupe([l1, l2, l3])
    assert len(deduped) == 2
    # Das günstigere Duplikat (14.500 €) muss behalten worden sein
    same_cars = [l for l in deduped if l.mileage == 85432]
    assert len(same_cars) == 1
    assert same_cars[0].price == 14500
