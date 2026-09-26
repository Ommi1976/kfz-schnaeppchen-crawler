"""Verfügbarkeitsprüfung: keine Portalzugriffe, echte SQLite-Logik."""
import pytest

from kfz_crawler import availability
from kfz_crawler.availability import classify, check_unseen
from kfz_crawler.models import Listing
from kfz_crawler.storage import SeenStore

DAY = 86400
AS24 = "https://www.autoscout24.de/angebote/vw-id3-123"
KA = "https://www.kleinanzeigen.de/s-anzeige/vw-id-3/3516596562-216-7315"
AU = "https://www.autouncle.de/de/r/abc"
MOBILE = "https://suchen.mobile.de/fahrzeuge/details.html?id=1"


@pytest.fixture
def store():
    db = SeenStore(":memory:")
    yield db
    db.close()


def record(store, portal, url, *, seen, search="E-Autos"):
    listing = Listing(portal=portal, title="EV", url=url, price=20000, year=2023, mileage=10000)
    store.record_listings(search, [listing])
    store.conn.execute("UPDATE deals SET last_seen = ? WHERE url = ?", (seen, url))
    store.conn.commit()
    return listing.fingerprint


def visible(store, include_stale=False):
    return {r["url"] for r in store.list_deals(include_stale=include_stale)}


@pytest.mark.parametrize("portal,url,status,final,expected", [
    # Gemessen am 26.09.2026
    ("AutoScout24", AS24, 410, AS24, "gone"),
    ("AutoScout24", AS24, 200, AS24, "alive"),
    ("AutoUncle", AU, 410, AU, "gone"),
    ("AutoUncle", AU, 200, AU, "alive"),
    ("Kleinanzeigen", KA, 200, "https://www.kleinanzeigen.de/s-autos/ronnenberg/c216l2902", "gone"),
    ("Kleinanzeigen", KA, 200, KA, "alive"),
    ("AutoScout24", AS24, 404, AS24, "gone"),
    # Sperre oder Serverfehler beweist nichts
    ("AutoScout24", AS24, 403, AS24, "unknown"),
    ("AutoUncle", AU, 429, AU, "unknown"),
    ("AutoScout24", AS24, 503, AS24, "unknown"),
    # Ungemessene Umleitung ist kein Beleg
    ("AutoScout24", AS24, 200, "https://www.autoscout24.de/lst/volkswagen", "unknown"),
])
def test_classify(portal, url, status, final, expected):
    assert classify(portal, url, status, final) == expected


def test_gone_listing_disappears_from_every_list(store):
    now = 10 * DAY
    gone = record(store, "AutoScout24", AS24, seen=now - 2 * DAY)
    alive = record(store, "Kleinanzeigen", KA, seen=now - 2 * DAY)
    responses = {AS24: (410, AS24), KA: (200, KA)}
    counts = check_unseen(store, "E-Autos", now - 60, fetch=lambda u: responses[u],
                          sleep=lambda s: None, now=now)
    assert counts["gone"] == 1 and counts["alive"] == 1
    assert visible(store) == {KA}
    # Auch die Ansicht mit veralteten Einträgen zeigt Gelöschtes nicht.
    assert visible(store, include_stale=True) == {KA}
    assert store.count_stale() == 0
    row = store.conn.execute("SELECT gone_at, alive_at FROM deals WHERE fingerprint = ?", (gone,)).fetchone()
    assert row["gone_at"] == now
    assert store.conn.execute("SELECT alive_at FROM deals WHERE fingerprint = ?", (alive,)).fetchone()[0] == now


def test_seen_again_listing_returns(store):
    now = 10 * DAY
    record(store, "AutoScout24", AS24, seen=now - 2 * DAY)
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: (410, u), sleep=lambda s: None, now=now)
    assert visible(store) == set()
    record(store, "AutoScout24", AS24, seen=now + 60)
    assert visible(store) == {AS24}


def test_blocked_check_keeps_listing_and_waits_before_recheck(store):
    now = 10 * DAY
    record(store, "AutoScout24", AS24, seen=now - 2 * DAY)
    calls = []

    def fetch(url):
        calls.append(url)
        raise TimeoutError("blocked")

    check_unseen(store, "E-Autos", now - 60, fetch=fetch, sleep=lambda s: None, now=now)
    assert visible(store) == {AS24}
    check_unseen(store, "E-Autos", now - 60, fetch=fetch, sleep=lambda s: None, now=now + 3600)
    assert len(calls) == 1  # frühestens nach RECHECK_AFTER erneut


def test_listings_seen_in_this_run_are_not_checked(store):
    now = 10 * DAY
    record(store, "AutoScout24", AS24, seen=now)
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: pytest.fail("kein Abruf erwartet"),
                 sleep=lambda s: None, now=now)
    assert visible(store) == {AS24}


def test_mobile_is_never_fetched_but_safety_net_applies(store):
    now = 10 * DAY
    record(store, "mobile.de", MOBILE, seen=now - 4 * DAY)
    store.record_portal_run("E-Autos", "mobile.de", "partial", raw_count=240)
    store.conn.execute("UPDATE portal_health SET last_run = ?", (now,))
    store.conn.commit()
    counts = check_unseen(store, "E-Autos", now - 60, fetch=lambda u: pytest.fail("mobile.de nie einzeln"),
                          sleep=lambda s: None, now=now)
    assert counts["stale"] == 1
    assert visible(store) == set()
    assert visible(store, include_stale=True) == {MOBILE}  # veraltet, nicht bewiesen gelöscht


def test_safety_net_needs_a_delivering_portal_run(store):
    now = 10 * DAY
    record(store, "mobile.de", MOBILE, seen=now - 4 * DAY)
    # Portal gesperrt: letzter Lauf ohne Treffer
    store.record_portal_run("E-Autos", "mobile.de", "blocked", raw_count=0)
    store.conn.execute("UPDATE portal_health SET last_run = ?", (now,))
    store.conn.commit()
    check_unseen(store, "E-Autos", now - 60, sleep=lambda s: None, now=now)
    assert visible(store) == {MOBILE}


def test_confirmed_alive_listing_survives_safety_net(store):
    now = 10 * DAY
    record(store, "AutoScout24", AS24, seen=now - 4 * DAY)
    store.record_portal_run("E-Autos", "AutoScout24", "ok", raw_count=88)
    store.conn.execute("UPDATE portal_health SET last_run = ?", (now,))
    store.conn.commit()
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: (200, u), sleep=lambda s: None, now=now)
    assert visible(store) == {AS24}


def test_cross_portal_link_hides_gone_offer(store):
    now = 10 * DAY
    source = record(store, "mobile.de", MOBILE, seen=now)
    other = record(store, "AutoScout24", AS24, seen=now - 2 * DAY)
    store.conn.execute("INSERT INTO vehicles (vehicle_id) VALUES ('v1')")
    for fp, portal, url in ((source, "mobile.de", MOBILE), (other, "AutoScout24", AS24)):
        store.conn.execute("INSERT INTO offers (offer_id, vehicle_id, portal, url, price) VALUES (?, 'v1', ?, ?, 20000)",
                           (fp, portal, url))
        store.conn.execute("INSERT INTO vehicle_links (vehicle_id, offer_id) VALUES ('v1', ?)", (fp,))
    store.conn.commit()
    assert [a["url"] for a in store.andere_angebote([MOBILE])[MOBILE]] == [AS24]
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: (410, u), sleep=lambda s: None, now=now)
    assert store.andere_angebote([MOBILE]) == {}
    status = store.conn.execute("SELECT status FROM offers WHERE offer_id = ?", (other,)).fetchone()[0]
    assert status == "entfernt"


def test_check_budget_per_portal(store):
    now = 10 * DAY
    for i in range(availability.CHECKS_PER_PORTAL + 3):
        record(store, "AutoScout24", f"{AS24}-{i}", seen=now - 2 * DAY)
    calls = []
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: calls.append(u) or (200, u),
                 sleep=lambda s: None, now=now)
    assert len(calls) == availability.CHECKS_PER_PORTAL


def test_safety_net_waits_for_check_on_checkable_portal(store):
    now = 10 * DAY
    record(store, "AutoUncle", AU, seen=now - 4 * DAY)
    store.record_portal_run("E-Autos", "AutoUncle", "ok", raw_count=495)
    store.conn.execute("UPDATE portal_health SET last_run = ?", (now,))
    store.conn.commit()
    # Noch nicht geprüft (Budget erschöpft): bleibt sichtbar.
    assert store.apply_unseen_safety_net("E-Autos", now - 3 * DAY, now - DAY, 3 * DAY, now,
                                         availability.CHECKABLE_PORTALS) == 0
    # Geprüft, aber unklar (z. B. gesperrt): jetzt greift das Netz.
    check_unseen(store, "E-Autos", now - 60, fetch=lambda u: (403, u), sleep=lambda s: None, now=now)
    assert visible(store) == set()
