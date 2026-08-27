import pytest
from kfz_crawler.models import (
    Listing,
    SearchQuery,
    extract_battery_kwh,
    extract_battery_soh,
    extract_ev_range_km,
    infer_listing_battery,
    infer_listing_range,
    is_non_pkw,
    matches_query,
)


def test_extract_battery_kwh():
    assert extract_battery_kwh("VW ID.3 Pro Performance 58 kWh") == 58.0
    assert extract_battery_kwh("Tesla Model 3 Long Range 78,5 kwh Batterie") == 78.5
    assert extract_battery_kwh("Hyundai Ioniq 5 77.4 kWh AWD") == 77.4
    assert extract_battery_kwh("Golf 7 TDI 150 PS") is None
    assert extract_battery_kwh("Auto mit 500 kWh") is None  # Unplausibel


def test_extract_battery_soh():
    assert extract_battery_soh("Mercedes EQB 300 Progressive SOH96") == 96.0
    assert extract_battery_soh("BMW i3 120Ah SoH: 94.5% Batteriezertifikat") == 94.5
    assert extract_battery_soh("Nissan Leaf Batteriezustand 97%") == 97.0
    assert extract_battery_soh("VW ID.3 Akkugesundheit: 92%") == 92.0
    assert extract_battery_soh("Tesla Model 3 State of Health 98%") == 98.0
    assert extract_battery_soh("Auto mit 10 % SoH") is None  # Unter 50% unplausibel
    assert extract_battery_soh(None) is None


def test_extract_ev_range_km():
    assert extract_ev_range_km("Reichweite bis zu 450 km nach WLTP") == 450
    assert extract_ev_range_km("Elektroauto 520 km Reichweite Top") == 520
    assert extract_ev_range_km("Benziner 800 km Gesamtreichweite") is None  # Nicht EV-spezifisch oder out-of-context
    assert extract_ev_range_km(None) is None


def test_infer_battery_and_range():
    l = Listing(portal="AS24", title="Kona Elektro 64 kWh 484 km Reichweite SoH: 95%", url="http://x")
    infer_listing_battery(l)
    infer_listing_range(l)
    assert l.battery_kwh == 64.0
    assert l.battery_soh == 95.0
    assert l.ev_range_km == 484


def test_is_non_pkw():
    assert is_non_pkw(Listing(portal="KA", title="E-Kabinenroller 45 km/h Elektro", url="http://x")) is True
    assert is_non_pkw(Listing(portal="KA", title="Seniorenmobil Elektromobil 25 km/h", url="http://x")) is True
    assert is_non_pkw(Listing(portal="KA", title="Lastenrad Trike E-Bike", url="http://x")) is True
    assert is_non_pkw(Listing(portal="KA", title="VW Golf 7 VII Limousine", url="http://x")) is False
    assert is_non_pkw(Listing(portal="KA", title="Tesla Model 3 Standard Range", url="http://x")) is False


def test_searchquery_serialization():
    data = {
        "id": "s123",
        "name": "Golf Diesel Saarbrücken",
        "active": True,
        "make": "volkswagen",
        "model": "golf",
        "exclude_makes": ["tesla", "smart"],
        "exclude_models": ["plus", "sportsvan"],
        "year_from": 2018,
        "year_to": 2022,
        "price_from": 10000,
        "price_to": 25000,
        "mileage_from": 0,
        "mileage_to": 120000,
        "fuel": "diesel",
        "transmission": "automatik",
        "body_type": "kombi",
        "power_from": 150,
        "power_to": 200,
        "seller": "haendler",
        "doors": "4/5",
        "zip_code": "66111",
        "radius_km": 100,
        "emission_class": "euro6",
        "drivetrain": "front",
        "include_damaged": False,
        "ev_range_from": None,
        "battery_from_kwh": None,
        "equipment": [1, 2, 5],
        "keywords": ["navi", "acc"],
        "exclude_terms": ["panoramadach"],
    }
    q = SearchQuery.from_dict(data)
    assert q.name == "Golf Diesel Saarbrücken"
    assert q.make == "volkswagen"
    assert q.zip_code == "66111"
    assert q.radius_km == 100
    assert q.equipment == [1, 2, 5]
    assert q.keywords == ["navi", "acc"]

    d_out = q.to_dict()
    assert d_out["zip_code"] == "66111"
    assert d_out["radius_km"] == 100
    assert d_out["fuel"] == "diesel"


def test_matches_query_filtering():
    q = SearchQuery(
        name="Golf Diesel Auto",
        make="volkswagen",
        model="golf",
        year_from=2017,
        year_to=2021,
        price_to=20000,
        mileage_to=150000,
        fuel="diesel",
        transmission="automatik",
        power_from=140,
        keywords=["navi"],
        exclude_terms=["schiebedach"],
    )

    # 1. Perfekter Treffer (VW Synonym funktioniert)
    l1 = Listing(
        portal="AS24",
        title="VW Golf VII 2.0 TDI DSG Navi Highline",
        url="http://x/1",
        price=16500,
        year=2019,
        mileage=85000,
        fuel="diesel",
        transmission="automatik",
        power_ps=150,
    )
    assert matches_query(l1, q) is True

    # 2. Falsche Marke
    l2 = Listing(portal="AS24", title="BMW 120d Automatik Navi", url="http://x/2", price=16000)
    assert matches_query(l2, q) is False

    # 3. Zu teuer
    l3 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi", url="http://x/3", price=22000)
    assert matches_query(l3, q) is False

    # 4. Ausschlussbegriff vorhanden
    l4 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi mit Schiebedach", url="http://x/4", price=16000)
    assert matches_query(l4, q) is False

    # 5. Zu wenig Leistung
    l5 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi", url="http://x/5", price=15000, power_ps=116)
    assert matches_query(l5, q) is False

    # 6. Defekt / Schaden / Motorschaden / Unfall
    l6 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Motorschaden*", url="http://x/6", price=8000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l6, q) is False

    # 7. Nur Export / Nur Händler
    l7 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Nur für Export*", url="http://x/7", price=9000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l7, q) is False

    # 8. Nur Import
    l8 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Nur für Import*", url="http://x/8", price=9000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l8, q) is False

    # 9. include_damaged = True erlaubt solche Fahrzeuge
    q_damaged = SearchQuery(name="Schrottis", make="volkswagen", model="golf", include_damaged=True)
    assert matches_query(l6, q_damaged) is True
