import json

import pytest

from kfz_crawler.listing_sources import autouncle_origin, listing_origin
from kfz_crawler.portals.autouncle import AutoUncle


@pytest.mark.parametrize("slug", ["mobile", "mobilebody", "mobilepricerating"])
def test_verified_source_routes_work_for_existing_listings(slug):
    url = f"https://www.autouncle.de/de/das_wiedersehen/{slug}/225351999/415210749"
    assert autouncle_origin(url) == "mobile.de"
    assert listing_origin("AutoUncle", url) == "mobile.de"


@pytest.mark.parametrize("url", [
    "https://autouncle.de.evil/de/das_wiedersehen/mobile/1/2",
    "https://evil@www.autouncle.de/de/das_wiedersehen/mobile/1/2",
    "https://www.autouncle.de:8443/de/das_wiedersehen/mobile/1/2",
    "https://www.autouncle.de/de/das_wiedersehen/mobile-autohaus/1/2",
    "https://www.autouncle.de/de/d/123-mobile.de-test",
    "https://www.autouncle.de/de/das_wiedersehen/dealer/1/2?source=mobile",
    "http://www.autouncle.de/de/das_wiedersehen/mobile/1/2",
])
def test_no_source_inferred_from_unrelated_or_untrusted_urls(url):
    assert autouncle_origin(url) is None


def test_card_source_evidence_is_scoped_to_card_and_preserves_url():
    html = '''<article><a href="/de/d/123-test"><h3>VW ID.4 Pro</h3></a>
        <p>2023 50.000 km Elektro 204 PS 25.000 € 77 kWh</p>
        <button data-testid="source-label"><span>mobile.de</span></button></article>
        <article><a href="/de/d/124-test"><h3>VW ID.3</h3></a>
        <p>Auch bei mobile.de inseriert. 2023 50.000 km 25.000 €</p></article>'''
    one, two = AutoUncle()._parse(html)
    assert one.portal == "AutoUncle"
    assert one.url == "https://www.autouncle.de/de/d/123-test"
    assert listing_origin(one.portal, one.url, one.field_evidence) == "mobile.de"
    assert listing_origin(two.portal, two.url, two.field_evidence) is None
    assert listing_origin("AutoUncle", two.url, one.field_evidence) is None
    assert listing_origin("AutoUncle", two.url, ["bad legacy evidence"]) is None


def test_non_autouncle_does_not_claim_an_indirect_source():
    assert listing_origin("mobile.de", "https://suchen.mobile.de/fahrzeuge/details.html?id=1") == "mobile.de"
    assert listing_origin("AutoScout24", "https://www.autouncle.de/de/das_wiedersehen/mobile/1/2") == "AutoScout24"
