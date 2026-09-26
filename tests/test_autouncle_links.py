"""AutoUncle-Direktlinks: ohne Portalzugriffe."""
from kfz_crawler.models import Listing
from kfz_crawler.portals.autouncle import AutoUncle
from kfz_crawler.storage import SeenStore

BASE = "https://www.autouncle.de"
# Auszug einer echten Fahrzeugseite (26.09.2026): "Zum Angebot" trägt dieselbe
# ID wie /de/d/<id>; die übrigen Links gehören zu "Ähnliche Fahrzeuge".
DETAIL = """
<a href="/de/das_wiedersehen/auto-zellmann/222276284/407636859">Ähnlich</a>
<a class="_gOAPY" href="/de/das_wiedersehen/autoscout24/223462080/419558140"><div>Zum Angebot</div></a>
<a href="/de/das_wiedersehen/azf-gruppe-de/226938418/418291873">Ähnlich</a>
"""


def listing(raw_id, url=None):
    return Listing(portal="AutoUncle", title="VW ID.4", raw_id=raw_id,
                   url=url or f"{BASE}/de/d/{raw_id}-gebraucht-2023-vw-id-4")


def test_direct_link_uses_the_same_vehicle_id_only():
    portal = AutoUncle()
    item = listing("223462080")
    portal._resolve_direct_links([item], fetch=lambda url: DETAIL, sleep=lambda s: None)
    assert item.url == f"{BASE}/de/das_wiedersehen/autoscout24/223462080/419558140"


def test_card_link_is_left_alone_and_missing_link_keeps_detail_page():
    portal = AutoUncle()
    direct = listing("1", f"{BASE}/de/das_wiedersehen/mobile/1/2")
    orphan = listing("999")
    calls = []
    portal._resolve_direct_links([direct, orphan], fetch=lambda url: calls.append(url) or DETAIL,
                                 sleep=lambda s: None)
    assert direct.url.endswith("/das_wiedersehen/mobile/1/2")
    assert orphan.url.startswith(f"{BASE}/de/d/999")  # kein fremder Link
    assert calls == [f"{BASE}/de/d/999"]


def test_known_link_comes_from_database_without_fetch():
    store = SeenStore(":memory:")
    try:
        known = listing("223462080", f"{BASE}/de/das_wiedersehen/autoscout24/223462080/419558140")
        store.record_listings("E-Autos", [known])
        portal = AutoUncle()
        portal.store = store
        fresh = listing("223462080")
        portal._resolve_direct_links([fresh], fetch=lambda url: (_ for _ in ()).throw(AssertionError("kein Abruf")),
                                     sleep=lambda s: None)
        assert fresh.url == known.url
    finally:
        store.close()


def test_budget_limits_detail_fetches():
    portal = AutoUncle()
    items = [listing(str(1000 + i)) for i in range(AutoUncle.LINK_BUDGET + 5)]
    calls = []
    portal._resolve_direct_links(items, fetch=lambda url: calls.append(url) or "", sleep=lambda s: None)
    assert len(calls) == AutoUncle.LINK_BUDGET


def test_fetch_error_keeps_listing():
    portal = AutoUncle()
    item = listing("223462080")

    def boom(url):
        raise TimeoutError("langsam")

    portal._resolve_direct_links([item], fetch=boom, sleep=lambda s: None)
    assert "/de/d/223462080" in item.url
