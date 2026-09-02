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


# --- Leasing und Miete ---------------------------------------------------
# Solche Angebote wurden bisher nur markiert, und auch das nur, wenn sie ins
# Preismodell kamen. Wer kaufen will, hat davon nichts.

MIETANGEBOTE = [
    "Tesla Model Y Leasingübernahme",
    "Tesla Model Y Leasingübnahme",                 # Tippfehler ohne "er"
    "BMW i4 Leasing Übernahme",                     # mit Leerzeichen
    "Tesla Model Y Long Range AWD Leasing",
    "BMW i4 Leasing Übernahme 0€ Anzahlung 650€ brutto monatl. M Paket",
    "Ionic 5 Restleasing bis 11/2027 und noch ca. 30tausend Kilometer",
    "Tesla Model Y Quicksilver Long Range Langzeitmiete Auto Abo",
    "Hyundai Ioniq 5 - Leasingübernahme",
    "BMW i4 eDrive 40 Leasingübernahme",
    "Tesla Model Y Premium- Leasingübernahme!",
]

KAUFANGEBOTE = [
    ("VW ID.3 Pro Performance 58 kWh", ""),
    ("Tesla Model 3 Leasingrückläufer aus erster Hand", ""),
    ("Hyundai Ioniq 5 Uniq", "Finanzierung und Leasing möglich, Preis 28.900 €"),
    ("Cupra Born 58 kWh", "Kein Leasing, direkter Verkauf"),
    # Eine Monatsrate ist kein Ausschlussgrund: Finanzierung fuehrt zum Kauf.
    ("VW ID.4 Pro ab 199 € mtl. Finanzierung", ""),
    ("Tesla Model 3 Highland, 349 EUR monatlich finanzieren", ""),
]


def test_mietangebote_werden_erkannt():
    from kfz_crawler.models import ist_mietangebot
    for titel in MIETANGEBOTE:
        assert ist_mietangebot(_inserat(titel)), titel


def test_kaufangebote_bleiben_erhalten():
    from kfz_crawler.models import ist_mietangebot
    for titel, text in KAUFANGEBOTE:
        inserat = Listing(portal="Kleinanzeigen", url="https://example.test/1",
                          title=titel, body=text)
        assert not ist_mietangebot(inserat), f"{titel} | {text}"


def test_mietangebot_faellt_aus_der_trefferliste():
    """Nicht nur markieren – der Filter muss es aussortieren."""
    query = SearchQuery(name="E-Autos", fuel="elektro")
    ergebnis = evaluate_query(
        _inserat("Tesla Model Y Leasingübernahme", preis=78), query
    )
    assert not ergebnis.passed
    assert "Leasing oder Miete, kein Kaufangebot" in ergebnis.reasons


def test_unvollstaendige_fahrzeuge_werden_ausgeschlossen():
    """Markieren reicht nicht – eine Rohkarosse für 1.200 € blieb sichtbar."""
    from kfz_crawler.models import is_defective_or_restricted
    for titel in [
        "Renault Megane E-Tech EV60 (220 PS) - RohKarosse",
        "VW ID.3 nur Karosserie",
        "BMW i3 Karosserie ohne Anbauteile",
        "Tesla Model 3 ohne Motor",
    ]:
        assert is_defective_or_restricted(_inserat(titel)), titel


def test_neue_karosserie_meint_die_neue_modellgeneration():
    """Vom Nutzer korrigiert: "neue Karosserie" ist kein Schadensmerkmal.

    Ebenso ist "Preis ohne Akku" bei der Zoe eine Batteriemiete – das Auto
    wird verkauft.
    """
    from kfz_crawler.models import is_defective_or_restricted
    for titel in [
        "Skoda Enyaq Coupe, neue Karosserie",
        "VW ID.4 Pro, Karosserie in Top-Zustand",
        "Renault Zoe Intens, Preis ohne Akku (Batteriemiete)",
    ]:
        assert not is_defective_or_restricted(_inserat(titel)), titel
