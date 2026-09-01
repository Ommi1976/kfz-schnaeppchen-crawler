"""Lockangebote und Zubehör: der konkrete Grund muss erkennbar sein."""
from kfz_crawler.dealfinder import fraud_reasons, _ist_platzhalterpreis
from kfz_crawler.models import Listing, SearchQuery, evaluate_query, is_zubehoer


def _inserat(titel, preis=None, jahr=None, km=None):
    return Listing(portal="Kleinanzeigen", url="https://example.test/1",
                   title=titel, price=preis, year=jahr, mileage=km)


def test_grund_statt_pauschalem_verdacht():
    """Der Text nennt meist den Grund – der gehört in die Anzeige."""
    def gruende(titel, preis=None):
        return fraud_reasons(_inserat(titel, preis))

    # Kein fahrbares Fahrzeug.
    assert gruende("Renault Megane E-Tech EV60 (220 PS) - RohKarosse") == \
        ["Rohkarosse (kein fahrbares Fahrzeug)"]
    assert "ohne Antrieb" in gruende("VW e-Golf ohne Motor")
    assert "ohne Akku" in gruende("BMW i3 ohne Akku")

    # Leasing: der Preis ist die Monatsrate, kein Kaufpreis.
    assert gruende("Tesla Model Y Leasingübernahme") == \
        ["Leasingübernahme (Preis = Monatsrate)"]
    assert gruende("Tesla Model Y Long Range AWD Leasing") == \
        ["Leasing (Preis = Monatsrate)"]

    # Ein normales Fahrzeug bleibt unauffällig.
    assert gruende("VW ID.4 Pro Performance Navi ACC") == []


def test_platzhalterpreise():
    """12.345 € ist kein Angebot, sondern ein nicht gepflegter Preis."""
    assert _ist_platzhalterpreis(12345)
    assert _ist_platzhalterpreis(11111)
    assert not _ist_platzhalterpreis(12500)
    assert not _ist_platzhalterpreis(None)


def test_zubehoer_wird_erkannt_aber_echte_autos_nicht():
    """Ausstattung im Titel darf kein Fahrzeug aussortieren."""
    assert is_zubehoer(_inserat("Wallbox 11kW Ladestation für Elektroauto"))
    assert is_zubehoer(_inserat("Typ-2 Ladekabel 22kW 5m"))
    assert is_zubehoer(_inserat("Winterkompletträder für VW ID.3 18 Zoll"))
    assert is_zubehoer(_inserat("Stoßstange vorne VW ID.4 original"))
    assert is_zubehoer(_inserat("Ersatzteile für Renault Zoe"))

    # Diese Fahrzeuge nennen Zubehör nur in der Ausstattung.
    assert not is_zubehoer(_inserat("VW ID.3 Pro S mit Winterreifen und AHK", jahr=2022, km=45000))
    assert not is_zubehoer(_inserat("Tesla Model Y inkl. Wallbox geschenkt", jahr=2023, km=30000))
    assert not is_zubehoer(_inserat("Renault Zoe Intens, Dachträger montiert", jahr=2021, km=60000))


def test_zubehoer_faellt_aus_der_suche():
    zubehoer = _inserat("Wallbox 11kW Ladestation", preis=450)
    entscheidung = evaluate_query(zubehoer, SearchQuery(name="EV", fuel="elektro"))
    assert not entscheidung.passed
    assert "Zubehör oder Ersatzteil, kein Fahrzeug" in entscheidung.reasons


def test_neue_karosserie_ist_kein_defekt():
    """"Neue Karosserie" meint die neue Modellgeneration – oder eine
    Instandsetzung. In beiden Fällen ist es kein Mangel."""
    unauffaellig = [
        "Skoda Enyaq Coupe, neue Karosserie",
        "VW ID.3 neues Modell 2024 Facelift",
        "Cupra Born neue Generation 77 kWh",
        "BMW i4 Modellpflege neue Scheinwerfer",
        "Hyundai Ioniq 5 neuer Akku eingebaut",
    ]
    for titel in unauffaellig:
        l = _inserat(titel, jahr=2024, km=30000)
        assert fraud_reasons(l) == [], titel
        assert not is_zubehoer(l), titel

    # Nur die eindeutigen Formulierungen greifen.
    assert fraud_reasons(_inserat("VW ID.3 nur Karosserie ohne Motor"))
    assert fraud_reasons(_inserat("Renault Megane - RohKarosse"))
