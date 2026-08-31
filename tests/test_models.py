import pytest
from kfz_crawler.models import (
    Listing,
    SearchQuery,
    extract_battery_kwh,
    extract_battery_soh,
    extract_ev_range_km,
    infer_listing_battery,
    infer_listing_details,
    infer_listing_range,
    is_non_pkw,
    evaluate_query,
    matches_query,
)


def test_extract_battery_kwh():
    assert extract_battery_kwh("VW ID.3 Pro Performance 58 kWh") == 58.0
    assert extract_battery_kwh("Tesla Model 3 Long Range 78,5 kwh Batterie") == 78.5
    assert extract_battery_kwh("Hyundai Ioniq 5 77.4 kWh AWD") == 77.4
    assert extract_battery_kwh("Golf 7 TDI 150 PS") is None
    assert extract_battery_kwh("Auto mit 500 kWh") is None  # Unplausibel
    # Stromverbrauch darf NIEMALS als Akkugröße erkannt werden
    assert extract_battery_kwh("153,0 kWh/100 km (komb.)") is None
    assert extract_battery_kwh("Verbrauch: 17.5 kWh / 100 km") is None
    assert extract_battery_kwh("14.2 kWh pro 100 km") is None
    assert extract_battery_kwh("16 kWh/100km kombiniert") is None


def test_extract_battery_soh():
    assert extract_battery_soh("Mercedes EQB 300 Progressive SOH96") == 96.0
    assert extract_battery_soh("BMW i3 120Ah SoH: 94.5% Batteriezertifikat") == 94.5
    assert extract_battery_soh("Nissan Leaf Batteriezustand 97%") == 97.0
    assert extract_battery_soh("VW ID.3 Akkugesundheit: 92%") == 92.0
    assert extract_battery_soh("Tesla Model 3 State of Health 98%") == 98.0
    # mobile.de Batterie-Information Widget
    assert extract_battery_soh("Batterie-Information Batterie-Status 94.6% Sehr gut Reichweite (WLTP) 546 km") == 94.6
    assert extract_battery_soh("Batterie-Status: 94,8 %") == 94.8
    assert extract_battery_soh("94.6% Sehr gut") == 94.6
    assert extract_battery_soh("94.6%") is None


def test_unknown_battery_is_kept_when_range_matches():
    listing = Listing(portal="KA", title="E-Auto", url="https://example.test/1", ev_range_km=470)
    tolerant = SearchQuery(name="EV", battery_from_kwh=62, ev_range_from=450)
    decision = evaluate_query(listing, tolerant)
    assert decision.passed is True
    assert "battery_kwh" in decision.unknown_fields

    strict = SearchQuery(name="EV", battery_from_kwh=62, ev_range_from=450, unknown_policy="strict")
    assert evaluate_query(listing, strict).passed is False
    # AVILOO / DEKRA Batteriezertifikat (OCR-Text)
    assert extract_battery_soh("GESUNDHEITSZUSTAND (SOH) 96,9 %") == 96.9
    assert extract_battery_soh("Gesundheitszustand (SOH) 94.5 %") == 94.5
    assert extract_battery_soh("GESUNDHEITSZUSTAND (SOH) 96,9 % ENERGIE 75kWh | 77kWh") == 96.9
    assert extract_battery_soh("SOH) 96,9 %") == 96.9  # OCR kann Klammer verschlucken
    assert extract_battery_soh("Auto mit 10 % SoH") is None  # Unter 50% unplausibel
    assert extract_battery_soh(None) is None
    # Erweiterte Erkennung (v1.0.2): Ziffern (Datum) zwischen Stichwort und Wert,
    # weitere Formulierungen, sowie robuste Negativfälle (kWh ist kein SoH).
    assert extract_battery_soh("Batteriezustand lt. AVILOO-Test vom 12.03.2024: 92 %") == 92.0
    assert extract_battery_soh("Restkapazität 90%") == 90.0
    assert extract_battery_soh("verbleibende Kapazität 86%") == 86.0
    assert extract_battery_soh("Batteriezertifikat: 89 %") == 89.0
    assert extract_battery_soh("Aviloo Score: 98") == 98.0
    assert extract_battery_soh("DEKRA Batterietest 95%") == 95.0
    assert extract_battery_soh("Akku liegt bei 91 %") == 91.0
    assert extract_battery_soh("19% MwSt. ausgewiesen") is None
    assert extract_battery_soh("100% unfallfrei") is None
    assert extract_battery_soh("Batteriegesundheit 97 %") == 97.0
    assert extract_battery_soh("94,6 % (SoH)") == 94.6
    assert extract_battery_soh("SOH: 96") == 96.0  # ohne Prozentzeichen
    assert extract_battery_soh("Batteriekapazität 62 kWh") is None  # kWh ≠ SoH
    assert extract_battery_soh("Akku 62 kWh, Reichweite 400 km") is None
    assert extract_battery_soh("Finanzierung mit 0% Anzahlung") is None


def test_extract_ev_range_km():
    assert extract_ev_range_km("Reichweite bis zu 450 km nach WLTP") == 450
    assert extract_ev_range_km("Elektroauto 520 km Reichweite Top") == 520
    assert extract_ev_range_km("Benziner 800 km Gesamtreichweite") is None  # Nicht EV-spezifisch oder out-of-context
    assert extract_ev_range_km(None) is None


def test_infer_battery_and_range():
    # 1. Explizit im Text
    l1 = Listing(portal="AS24", title="Kona Elektro 64 kWh 484 km Reichweite SoH: 95%", url="http://x")
    infer_listing_battery(l1)
    infer_listing_range(l1)
    assert l1.battery_kwh == 64.0
    assert l1.battery_soh == 95.0
    assert l1.ev_range_km == 484

    # 2. Aus Modell-Katalog abgeleitet (ohne explizite kWh-Nennung im Text)
    l2 = Listing(portal="mobile.de", title="Volkswagen ID.3 Pure Performance LED ACC NAVI", url="http://x")
    infer_listing_battery(l2)
    infer_listing_range(l2)
    assert l2.battery_kwh == 55.0
    assert l2.ev_range_km == 352

    l3 = Listing(portal="mobile.de", title="Volkswagen ID.4 Pro Performance Matrix IQ", url="http://x")
    infer_listing_battery(l3)
    infer_listing_range(l3)
    assert l3.battery_kwh == 82.0
    assert l3.ev_range_km == 522


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

    # 6. Defekt / Schaden / Motorschaden / Unfall / Beschädigtes Fahrzeug
    l6 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Motorschaden*", url="http://x/6", price=8000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l6, q) is False

    l6_beschaedigt = Listing(portal="KA", title="Volkswagen ID.3 Pro S", body="Volkswagen ID.3 Pro S 150 kW Beschädigtes Fahrzeug", url="http://x/6b", price=5600)
    assert matches_query(l6_beschaedigt, q) is False

    l6_glasschaden = Listing(portal="KA", title="VW ID.3 Facelift Glasschaden!!", url="http://x/6c", price=12000)
    assert matches_query(l6_glasschaden, q) is False

    # 7. Nur Export / Nur Händler
    l7 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Nur für Export*", url="http://x/7", price=9000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l7, q) is False

    # 8. Nur Import
    l8 = Listing(portal="AS24", title="VW Golf 7 TDI DSG Navi *Nur für Import*", url="http://x/8", price=9000, year=2019, mileage=85000, fuel="diesel", transmission="automatik", power_ps=150)
    assert matches_query(l8, q) is False

    # 9. include_damaged = True erlaubt solche Fahrzeuge
    q_damaged = SearchQuery(name="Schrottis", make="volkswagen", model="golf", include_damaged=True)
    assert matches_query(l6, q_damaged) is True

    # 10. E-Auto Filter: Strikte Prüfung aller gesetzten Kriterien (z.B. Akku >= 65 kWh & Reichweite >= 450 km)
    q_ev = SearchQuery(name="E-Autos", fuel="elektro", battery_from_kwh=65.0, ev_range_from=450)
    # GWM mit 310 km Reichweite muss abgewiesen werden
    l_gwm = Listing(portal="AS24", title="GWM Sonstige", url="http://x/gwm", fuel="elektro", ev_range_km=310, battery_kwh=None)
    assert matches_query(l_gwm, q_ev) is False

    # Mini mit 203 km Reichweite muss abgewiesen werden
    l_mini = Listing(portal="AS24", title="MINI Cooper SE", url="http://x/mini", fuel="elektro", ev_range_km=203, battery_kwh=32.6)
    assert matches_query(l_mini, q_ev) is False

    # Audi e-tron 50 mit 282 km Reichweite (aber 71 kWh Akku) muss abgewiesen werden (Reichweite zu gering)
    l_etron = Listing(portal="AS24", title="Audi e-tron 50", url="http://x/etron", fuel="elektro", ev_range_km=282, battery_kwh=71.0)
    assert matches_query(l_etron, q_ev) is False

    # ID.4 mit 522 km und 82 kWh muss akzeptiert werden
    l_id4 = Listing(portal="AS24", title="VW ID.4", url="http://x/id4", fuel="elektro", ev_range_km=522, battery_kwh=82.0)
    assert matches_query(l_id4, q_ev) is True

    # ID.3 mit 420 km und 58 kWh muss abgewiesen werden
    l_id3_small = Listing(portal="AS24", title="VW ID.3", url="http://x/id3", fuel="elektro", ev_range_km=420, battery_kwh=58.0)
    assert matches_query(l_id3_small, q_ev) is False

    # 11. Kleinanzeigen kennt keine verlässlichen strukturierten
    # Ausstattungsdaten. Fehlende Begriffe sind daher "unbekannt", nicht
    # "fehlt"; das Inserat bleibt in der Ergebnisliste.
    q_eq = SearchQuery(name="Mit Ausstattung", equipment=[34, 133])  # 34: Sitzheizung, 133: Abstandstempomat (ACC)
    l_ka_match = Listing(portal="Kleinanzeigen", title="VW Golf 8 mit SHZ und ACC Abstandstempomat", body="Top Zustand", url="http://ka/1")
    assert matches_query(l_ka_match, q_eq) is True

    l_ka_missing = Listing(portal="Kleinanzeigen", title="VW Golf 8 Basis", body="Nur Radio und Klima", url="http://ka/2")
    assert matches_query(l_ka_missing, q_eq) is True


def test_classify_battery_kind_uses_nearest_marker():
    """Die Bezugsgröße kommt von der Wortmarke neben der jeweiligen Zahl."""
    from kfz_crawler.models import classify_battery_kind as kind

    assert kind("VW ID.3 Pro S 77 kWh netto", 77.0) == "netto"
    assert kind("Akku 82 kWh brutto", 82.0) == "brutto"
    # Beide Größen im selben Text: jede Zahl behält ihre eigene Marke.
    assert kind("77 kWh netto (82 kWh brutto)", 77.0) == "netto"
    assert kind("77 kWh netto (82 kWh brutto)", 82.0) == "brutto"
    # Ohne Marke entscheidet der Abgleich mit den Referenzwerten des Modells.
    assert kind("ID.3 Pro S 77kWh(82kWh)", 77.0, 77.0, 82.0) == "netto"
    assert kind("ID.3 Pro S 77kWh(82kWh)", 82.0, 77.0, 82.0) == "brutto"
    # Ohne beides wird nicht geraten.
    assert kind("Elektro 62 kWh Navi", 62.0) == "unbekannt"
    # "Nettopreis" ist keine Kapazitätsangabe.
    assert kind("Nettopreis 20000 Euro, Akku 62 kWh", 62.0) == "unbekannt"
    # Eine Marke darf nicht von der Nachbarzahl geliehen werden.
    assert kind("Golf 77 kWh, daneben 45 kWh netto Zusatz", 77.0) == "unbekannt"


def test_battery_filter_never_compares_netto_against_brutto_threshold():
    """Ein reiner Nettowert schließt nicht aus, sondern gilt als unbekannt."""
    from kfz_crawler.models import battery_for_filter, evaluate_query

    query = SearchQuery(name="EV", battery_from_kwh=65)

    # Brutto bekannt: normaler Vergleich.
    gross = Listing(portal="p", title="ID.4", url="u", battery_gross_kwh=82.0)
    assert battery_for_filter(gross) == 82.0
    assert evaluate_query(gross, query).passed

    # Nur ein Nettowert: die Bruttokapazität liegt höher, also nicht ausschließen.
    netto = Listing(portal="p", title="ID.3", url="u", battery_kwh=64.0)
    netto.battery_observed_kind = "netto"
    assert battery_for_filter(netto) is None
    entscheidung = evaluate_query(netto, query)
    assert "Akkukapazität unter Mindestwert" not in entscheidung.reasons

    # Unbekannte Bezugsgröße bleibt vergleichbar (bisheriges Verhalten).
    unbekannt = Listing(portal="p", title="Zoe", url="u", battery_kwh=52.0)
    assert battery_for_filter(unbekannt) == 52.0
    assert "Akkukapazität unter Mindestwert" in evaluate_query(unbekannt, query).reasons


def test_classify_range_standard_binds_marker_to_its_number():
    """Der Messstandard gehört zu der Zahl, neben der er steht."""
    from kfz_crawler.models import classify_range_standard as std

    assert std("Reichweite 520 km WLTP", 520) == "wltp"
    assert std("450 km nach NEFZ", 450) == "nefz"
    assert std("EPA 380 km", 380) == "epa"
    assert std("real 310 km im Alltag", 310) == "real"
    # Klammer und Doppelpunkt gehören zum Label, nicht zur Trennung.
    assert std("Reichweite (WLTP) 546 km", 546) == "wltp"
    assert std("WLTP kombiniert: 546 km", 546) == "wltp"
    # Ein Komma trennt: die Marke der Vorgängerzahl darf nicht gelten.
    assert std("520 km WLTP, Anhängelast 750 km", 750) == "unbekannt"
    assert std("520 km WLTP, Anhängelast 750 km", 520) == "wltp"
    # Ohne Marke wird nicht geraten.
    assert std("Reichweite 400 km", 400) == "unbekannt"


def test_model_year_is_not_treated_as_first_registration():
    """Ein Modelljahr ist keine Erstzulassung – Fahrzeuge werden später zugelassen."""
    from kfz_crawler.models import extract_first_registration as ez

    assert ez("EZ 04/2021 Diesel") == (2021, 4, "ez")
    assert ez("Erstzulassung 2022") == (2022, None, "ez")
    assert ez("Modelljahr 2022 Neuwagen") == (2022, None, "modelljahr")
    # Blosse Jahreszahl im Titel ist kein Beleg.
    assert ez("VW Golf 2019 Navi") == (None, None, "unbekannt")
    # Unplausibler Monat wird verworfen, das Jahr bleibt.
    assert ez("EZ 13/2021") == (2021, None, "ez")

    listing = Listing(portal="p", title="Renault Zoe Modelljahr 2020", url="u")
    infer_listing_details(listing)
    assert listing.year_kind == "modelljahr"


def test_soh_evidence_levels():
    """Ein Prozentwert ohne Batteriekontext gilt nicht als belegter SoH."""
    from kfz_crawler.models import classify_soh_level as lvl

    assert lvl("AVILOO Batteriezertifikat SoH 94,6 %", 94.6) == "bestaetigt"
    assert lvl("DEKRA Batterietest 95%", 95.0) == "bestaetigt"
    assert lvl("Batteriezustand 97%", 97.0) == "belegt"
    assert lvl("Restkapazität 90%", 90.0) == "belegt"
    assert lvl("94,6 % sehr gut", 94.6) == "kandidat"
    # Ladestand-Screenshot: kein bestätigter SoH.
    assert lvl("Sofortladen bis 100% - 51 km 16%", 90.0) == "kandidat"
    assert lvl("irgendwas", None) == "unbekannt"

    listing = Listing(portal="p", url="u",
                      title="VW ID.4 AVILOO Batteriezertifikat SoH 94,6 %")
    infer_listing_details(listing)
    assert listing.battery_soh == 94.6
    assert listing.battery_soh_level == "bestaetigt"
