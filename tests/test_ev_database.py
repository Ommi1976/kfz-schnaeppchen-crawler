from kfz_crawler.models import Listing, infer_listing_battery, infer_listing_range
from kfz_crawler.ev_database import lookup_ev_spec, lookup_ev_spec_match

def test_ev_database_lookup():
    spec1 = lookup_ev_spec("Volkswagen ID.3 Pure Performance 110 kW LED NAVI")
    assert spec1 is not None
    assert spec1.model == "ID.3"
    assert spec1.battery_gross_kwh == 55.0
    assert spec1.wltp_range_km == 352

    spec2 = lookup_ev_spec("Tesla Model 3 Long Range AWD")
    assert spec2 is not None
    assert spec2.model == "Model 3"
    assert spec2.battery_gross_kwh == 78.5
    assert spec2.wltp_range_km == 602

    spec3 = lookup_ev_spec("Mercedes EQB 300 4MATIC")
    assert spec3 is not None
    assert spec3.battery_gross_kwh == 66.5

def test_priority_listing_overrides_database():
    # Inserat hat abweichenden/speziellen Akkuwert im Text -> Inseratswert hat Vorrang!
    l = Listing(
        portal="mobile.de",
        title="Volkswagen ID.3 Pro mit speziellem 60 kWh Akku",
        url="http://x",
    )
    infer_listing_battery(l)
    assert l.battery_kwh == 60.0  # 60 kWh aus dem Inserat, nicht die 62 kWh aus DB

def test_fallback_database_when_no_text_kwh():
    l = Listing(
        portal="mobile.de",
        title="Volkswagen ID.4 Pro Performance Matrix",
        url="http://x",
    )
    infer_listing_battery(l)
    infer_listing_range(l)
    assert l.battery_kwh == 82.0
    assert l.ev_range_km == 522


def test_cupra_born_capacity_variant_is_not_promoted_to_large_battery():
    """58/62-kWh Born darf nicht als 77/82-kWh Born mit 548 km erscheinen."""
    small = lookup_ev_spec("CUPRA Born 150 (58kWh)")
    assert small is not None
    assert small.battery_gross_kwh == 62.0
    assert small.battery_net_kwh == 58.0
    assert small.wltp_range_km == 425

    l_small = Listing(portal="AutoScout24", title="CUPRA Born 150 (58kWh)", url="http://as24/born-small")
    infer_listing_battery(l_small)
    infer_listing_range(l_small)
    assert l_small.battery_kwh == 58.0
    assert l_small.ev_range_km == 425

    large = lookup_ev_spec("Cupra Born 82 kWh e-Boost")
    assert large is not None
    assert large.battery_gross_kwh == 82.0
    assert large.wltp_range_km == 548

    # 170 kW/e-Boost allein ist bei beiden Batteriegrößen möglich und darf
    # daher nicht mehr fälschlich als große Variante ausgegeben werden.
    assert lookup_ev_spec("Cupra Born e-Boost 170 kW") is None


def test_match_exposes_confidence_and_net_gross_evidence():
    match = lookup_ev_spec_match("Cupra Born 58 kWh 150 kW")
    assert match is not None
    assert match.confidence >= 0.95
    listing = Listing(portal="mobile.de", title="Cupra Born 58 kWh 150 kW", url="https://example.test/born")
    infer_listing_battery(listing)
    assert listing.battery_net_kwh == 58.0
    assert listing.battery_gross_kwh == 62.0
    assert listing.field_evidence["battery_kwh"]["source"] == "title"


def test_byd_dolphin_surf_wltp_plausibility():
    # Händler schreibt 460 km Reichweite (City/Marketing), echter WLTP Kombiniert ist 310 km (44.9/43.2 kWh)
    l = Listing(
        portal="AutoScout24",
        title="BYD Dolphin Surf Comfort Alu LED Link NAV NBA PDC RFK SHA Shz",
        body="28 km Automatik 01/2026 Elektro 115 kW (156 PS) 460 km Reichweite",
        url="http://as24/byd",
    )
    infer_listing_battery(l)
    infer_listing_range(l)
    # Muss durch Referenzdatenbank auf echten Akkuwert und echten WLTP-Kombiniert-Wert korrigiert werden
    assert l.battery_kwh == 44.9
    assert l.ev_range_km == 310


# --- Modell + Leistung statt nur Kapazität -------------------------------
# Bis hierher verlangte jedes Muster die kWh-Zahl im Text
# (\bborn\b.*?\b(?:77|79|82|84)\s*kwh\b). Die Datenbank konnte eine Kapazität
# damit nur bestätigen, nie erschließen: "Cupra Born 231 PS" ergab nichts,
# obwohl zu allen 124 Varianten die Leistung hinterlegt ist. In der Folge blieb
# die Akkuspalte leer und ein Filter "mindestens 65 kWh" wirkungslos.

def test_leistung_bestimmt_die_variante():
    from kfz_crawler.ev_database import lookup_ev_spec_match
    faelle = [
        ("Gebraucht (2022) Cupra Born 231 PS | Superpreis", 231, 77.0),
        ("Cupra Born", 204, 58.0),
        ("Cupra Born", 150, 45.0),
        ("Hyundai Ioniq 5 170 PS", 170, 54.0),
        ("Skoda Enyaq iV 80 Loft", 204, 77.0),
    ]
    for titel, ps, netto in faelle:
        treffer = lookup_ev_spec_match(titel, "", power_ps=ps)
        assert treffer is not None, titel
        assert treffer.spec.battery_net_kwh == netto, f"{titel}: {treffer.spec.variant}"


def test_ohne_leistung_wird_nichts_geraten():
    """Mehrdeutig bleibt unbekannt – ein Fantasiewert wäre schlimmer."""
    from kfz_crawler.ev_database import lookup_ev_spec_match
    assert lookup_ev_spec_match("Cupra Born", "") is None


def test_polestar_231_ps_ist_der_long_range():
    """Gemessen: vier Inserate bekamen 69 kWh statt 78 zugeschrieben.

    Die Standard Range trug fälschlich 231 PS, und der Long Range Single Motor
    fehlte ganz. Beim Polestar 2 sind 231 PS der Long Range mit 78 kWh.
    """
    from kfz_crawler.ev_database import lookup_ev_spec_match
    treffer = lookup_ev_spec_match("Gebraucht (2022) Polestar 2 231 PS", "", power_ps=231)
    assert treffer.spec.battery_net_kwh == 78.0


def test_kia_ev6_trennt_die_beiden_akkugroessen():
    """Ein einziger Eintrag deckte 58 und 77,4 kWh ab – und lieferte immer 77,4."""
    from kfz_crawler.ev_database import lookup_ev_spec_match
    klein = lookup_ev_spec_match("Kia EV6 58 kWh 2WD", "", power_ps=170)
    gross = lookup_ev_spec_match("Kia EV6 GT-Line", "", power_ps=229)
    assert klein.spec.battery_net_kwh == 54.0
    assert gross.spec.battery_net_kwh == 74.0


def test_baujahr_trennt_gleich_starke_varianten():
    """Der Lexus UX 300e bekam die 72,8 kWh des Modelljahrs 2023.

    Bis 2022 hat er 64,8 kWh – bei unveränderter Leistung. Er verfehlt damit
    eine 65-kWh-Grenze, bestand sie aber, weil die Datenbank keine Modelljahre
    kannte. Beim BMW i3 ist es dasselbe: 22, 33 und 42 kWh bei stets 170 PS.
    """
    from kfz_crawler.ev_database import lookup_ev_spec_match
    def brutto(titel, ps, jahr):
        treffer = lookup_ev_spec_match(titel, "", power_ps=ps, year=jahr)
        return treffer.spec.battery_gross_kwh if treffer else None

    assert brutto("Lexus UX 300e 204 PS", 204, 2022) == 64.8
    assert brutto("Lexus UX 300e 204 PS", 204, 2023) == 72.8
    assert brutto("BMW i3", 170, 2015) == 22.0
    assert brutto("BMW i3", 170, 2017) == 33.2
    assert brutto("BMW i3", 170, 2020) == 42.2


def test_ohne_baujahr_bleibt_die_zuordnung_offen():
    """Zwei Varianten, die nur das Baujahr trennt – ohne Jahr kein Wert."""
    from kfz_crawler.ev_database import lookup_ev_spec_match
    assert lookup_ev_spec_match("Lexus UX 300e", "", power_ps=204) is None


def test_variante_schlaegt_das_kuerzere_modellwort():
    """"Pro S" erklärt mehr vom Titel als "Pro".

    Zuvor gewann "Pro", weil sein Muster ausführlicher notiert ist – die
    Zuordnung galt dann als mehrdeutig und die Kapazität blieb unbekannt.
    """
    from kfz_crawler.ev_database import lookup_ev_spec_match
    treffer = lookup_ev_spec_match("VW ID.3 Pro S", "", power_ps=204)
    assert treffer is not None
    assert treffer.spec.battery_gross_kwh == 82.0
