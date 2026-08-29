from kfz_crawler.models import Listing, infer_listing_battery, infer_listing_range
from kfz_crawler.ev_database import lookup_ev_spec

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
