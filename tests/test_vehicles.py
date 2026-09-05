import pytest
from kfz_crawler.vehicles import identitaets_score, SCHWELLE_SICHER


def _angebot(**werte):
    """Minimales Angebot als dict-artige Zeile."""
    basis = dict(portal="mobile.de", title="", year=None, mileage=None, power_ps=None,
                 fuel=None, price=None, location_zip=None, image_urls=None,
                 battery_gross_kwh=None, battery_net_kwh=None)
    basis.update(werte)
    return basis


def test_verschiedene_hersteller_werden_nie_zusammengefuehrt():
    """Ein ID.4 ist kein Q4 e-tron, auch bei zufällig gleichen Eckdaten."""
    a = _angebot(portal="AutoUncle", title="Gebraucht (2023) VW ID.4 Pro Performance LED Navi",
                 year=2023, mileage=82000, power_ps=204, price=25890, fuel="elektro")
    b = _angebot(portal="AutoScout24", title="Audi Q4 e-tron Sportback 150 kW LED Navi",
                 year=2023, mileage=82100, power_ps=204, price=26880, fuel="elektro")
    punkte, belege = identitaets_score(a, b)
    assert punkte == 0.0
    assert any("Hersteller" in b_ for b_ in belege)


def test_kilometer_toleranz_ist_absolut():
    """2 % Toleranz wären bei 82.000 km über 1.600 km – viel zu viel."""
    a = _angebot(title="VW ID.4 Pro", year=2023, mileage=82000, power_ps=204, fuel="elektro")
    b = _angebot(portal="AutoUncle", title="VW ID.4 Pro", year=2023, mileage=83000,
                 power_ps=204, fuel="elektro")
    assert identitaets_score(a, b)[0] == 0.0          # 1.000 km Unterschied
    b["mileage"] = 82300
    assert identitaets_score(a, b)[0] > 0.0           # 300 km sind plausibel


def test_ausstattung_zaehlt_nicht_als_modellkennung():
    """'LED' und 'Navi' sagen nichts über das Modell aus."""
    a = _angebot(title="VW ID.3 LED Navi ACC SHZ", year=2023, mileage=50000, fuel="elektro")
    b = _angebot(portal="AutoUncle", title="VW ID.5 LED Navi ACC SHZ", year=2023,
                 mileage=50100, fuel="elektro")
    punkte, belege = identitaets_score(a, b)
    assert punkte < SCHWELLE_SICHER
    assert not any("Modellwörter" in x for x in belege)


def test_gleiches_fahrzeug_auf_zwei_portalen_wird_zusammengefuehrt():
    """Ohne gemeinsame Bilder muss die Schwelle trotzdem erreichbar sein."""
    gemeinsam = dict(year=2022, mileage=73835, power_ps=150, fuel="elektro",
                     location_zip="66111", battery_gross_kwh=82.0)
    a = _angebot(portal="mobile.de", title="Volkswagen ID.4 Pro Performance",
                 price=27490, **gemeinsam)
    b = _angebot(portal="AutoUncle", title="Gebraucht (2022) Volkswagen ID.4 Pro Performance",
                 price=27900, **gemeinsam)
    punkte, belege = identitaets_score(a, b)
    assert punkte >= SCHWELLE_SICHER, (punkte, belege)


def test_andere_angebote_findet_dasselbe_auto_auf_anderen_portalen(tmp_path):
    """Die Verknüpfung lag in vehicle_links bereit, wurde aber nie gezeigt.

    Dasselbe Auto stand deshalb zweimal in der Liste, und der günstigere Preis
    fiel nicht auf.
    """
    from kfz_crawler.storage import SeenStore

    store = SeenStore(str(tmp_path / "fz.sqlite"))
    with store._lock:
        store.conn.execute(
            "INSERT INTO vehicles (vehicle_id, make, model) VALUES ('v1','Cupra','Born')")
        for offer, portal, preis, url in [
            ("o1", "mobile.de", 25699, "https://mobile.test/1"),
            ("o2", "Kleinanzeigen", 24990, "https://ka.test/1"),
            ("o3", "mobile.de", 31000, "https://mobile.test/2"),   # anderes Auto
        ]:
            store.conn.execute(
                "INSERT INTO offers (offer_id, vehicle_id, portal, price, url, status) "
                "VALUES (?,?,?,?,?,'aktiv')",
                (offer, "v1" if offer != "o3" else "v2", portal, preis, url))
        for offer in ("o1", "o2"):
            store.conn.execute(
                "INSERT INTO vehicle_links (offer_id, vehicle_id, confidence) VALUES (?,?,0.9)",
                (offer, "v1"))
        store.conn.execute(
            "INSERT INTO vehicle_links (offer_id, vehicle_id, confidence) VALUES ('o3','v2',0.9)")
        store.conn.commit()

    treffer = store.andere_angebote(["https://mobile.test/1", "https://mobile.test/2"])
    assert list(treffer) == ["https://mobile.test/1"]
    anderes = treffer["https://mobile.test/1"]
    assert len(anderes) == 1
    assert anderes[0]["portal"] == "Kleinanzeigen"
    assert anderes[0]["price"] == 24990          # 709 € günstiger
    store.close()


def test_andere_angebote_ohne_treffer_bleibt_leer(tmp_path):
    from kfz_crawler.storage import SeenStore
    store = SeenStore(str(tmp_path / "leer.sqlite"))
    assert store.andere_angebote([]) == {}
    assert store.andere_angebote(["https://example.test/x"]) == {}
    store.close()
