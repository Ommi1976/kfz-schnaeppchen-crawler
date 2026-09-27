"""AutoUncle -> mobile.de: ohne Portalzugriffe."""
import pytest

from kfz_crawler.mobile_bridge import bridge, mobile_id_from_redirect
from kfz_crawler.models import Listing
from kfz_crawler.storage import SeenStore

AU_URL = "https://www.autouncle.de/de/das_wiedersehen/mobile/226903543/418154140"
# Auszug einer echten Weiterleitungsseite (27.09.2026), einmal roh, einmal JSON-maskiert.
REDIRECT = ('<a href="https://suchen.mobile.de/auto-inserat/cupra-born-58-kwh-170-kw-eboost-garantie-'
            'l%C3%BCneburg/45180573055520.html?utm_campaign=AFF">weiter</a>'
            '<script>{"url":"https:\\/\\/suchen.mobile.de\\/auto-inserat\\/cupra-born\\/45180573055520.html"}</script>')
DETAIL = ("<html><body><h1>Cupra Born 58 kWh 170 kW eBoost</h1>"
          "<p>Fahrzeugbeschreibung laut Anbieter: Batteriezertifikat SoH 96 %. Erstzulassung 06/2022,"
          " Kilometerstand 49.000 km. Batteriekapazität 58 kWh.</p></body></html>")


@pytest.fixture
def store():
    db = SeenStore(":memory:")
    yield db
    db.close()


def autouncle_row(store, url=AU_URL):
    item = Listing(portal="AutoUncle", title="Gebraucht (2022) Cupra Born e-Boost 231 PS", url=url,
                   price=23900, year=2022, mileage=49000, raw_id="226903543")
    store.record_listings("E-Autos", [item])
    return item


def test_mobile_id_from_redirect():
    assert mobile_id_from_redirect(REDIRECT) == "45180573055520"
    assert mobile_id_from_redirect("<p>keine Adresse</p>") is None
    two = REDIRECT + "suchen.mobile.de/auto-inserat/x/99999999.html"
    assert mobile_id_from_redirect(two) is None  # mehrdeutig -> nichts raten


def test_bridge_creates_mobile_row_and_links_vehicle(store):
    au = autouncle_row(store)
    counts = bridge(store, "E-Autos", redirect_fetch=lambda u: REDIRECT,
                    detail_fetch=lambda u: DETAIL, now=1000.0)
    assert counts["übernommen"] == 1
    row = store.conn.execute("SELECT * FROM deals WHERE portal='mobile.de'").fetchone()
    assert row["url"].endswith("id=45180573055520")
    assert row["title"] == "Cupra Born 58 kWh 170 kW eBoost"
    assert row["price"] == 23900 and row["mileage"] == 49000
    assert row["battery_soh"] == 96
    assert store.autouncle_twins_of_mobile() == {au.fingerprint: {row["fingerprint"]}}
    # Zweiter Klick: nichts mehr zu tun.
    again = bridge(store, "E-Autos", redirect_fetch=lambda u: pytest.fail("kein Abruf"),
                   detail_fetch=lambda u: pytest.fail("kein Abruf"), now=2000.0)
    assert again["übernommen"] == 0


def test_unresolvable_redirect_is_not_retried_immediately(store):
    autouncle_row(store)
    calls = []
    bridge(store, "E-Autos", redirect_fetch=lambda u: calls.append(u) or "", now=1000.0,
           detail_fetch=lambda u: pytest.fail("ohne ID kein mobile.de-Abruf"))
    bridge(store, "E-Autos", redirect_fetch=lambda u: calls.append(u) or "", now=5000.0,
           detail_fetch=lambda u: pytest.fail("ohne ID kein mobile.de-Abruf"))
    assert len(calls) == 1


def test_block_stops_bridge_without_losing_the_id(store):
    from kfz_crawler.browser import BrowserBlocked
    from kfz_crawler.mobile_runtime import load_state
    au = autouncle_row(store)

    def blocked(url):
        raise BrowserBlocked("403")

    counts = bridge(store, "E-Autos", redirect_fetch=lambda u: REDIRECT, detail_fetch=blocked, now=1000.0)
    assert counts["vertagt"] == 1 and counts["übernommen"] == 0
    assert load_state(store, "autouncle.mobile.v1." + au.fingerprint)["mobile_id"] == "45180573055520"
    assert store.conn.execute("SELECT COUNT(*) FROM deals WHERE portal='mobile.de'").fetchone()[0] == 0
