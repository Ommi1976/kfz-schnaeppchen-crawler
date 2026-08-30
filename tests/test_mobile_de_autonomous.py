import pytest
from unittest.mock import patch
from kfz_crawler.models import SearchQuery
from kfz_crawler.portals.mobile_de import MobileDe
from kfz_crawler.portals.base import PortalPartialError

SAMPLE_MOBILE_HTML = """
<html>
<body>
    <article data-testid="result-listing">
        <a href="/fahrzeuge/details.html?id=11223344">
            <h2 data-testid="listing-title-card-view">Volkswagen Golf VIII 2.0 TDI DSG Life</h2>
        </a>
        <span data-testid="price-label">19.890 €</span>
        <div data-testid="listing-details">Unfallfrei • EZ 04/2021 • 54.000 km • 110 kW (150 PS) • Diesel</div>
        <div data-testid="seller-info">Autohaus Müller, 66111 Saarbrücken</div>
    </article>
</body>
</html>
"""

def test_mobile_de_autonomous_search():
    with patch("kfz_crawler.portals.mobile_de.MobileDe._fetch", return_value=SAMPLE_MOBILE_HTML):
        portal = MobileDe(max_pages=1)
        q = SearchQuery(name="Golf", make="volkswagen", model="golf")
        listings = portal.search(q)
        
        assert len(listings) == 1
        l = listings[0]
        assert l.portal == "mobile.de"
        assert "Golf VIII" in l.title
        assert l.price == 19890
        assert l.year == 2021
        assert l.mileage == 54000
        assert l.fuel == "diesel"
        assert l.power_ps == 150
        # Standort wird auf 'PLZ Stadt' normalisiert, damit die Entfernung
        # berechnet werden kann (Händlername wird entfernt).
        assert l.location == "66111 Saarbrücken"
        assert l.raw_id == "11223344"


def test_mobile_de_crawls_past_configured_page_sample():
    """Fünf konfigurierte Seiten dürfen keine Treffer ab Seite sechs verlieren."""
    def fetch_page(url):
        page = int(url.split("pageNumber=")[1].split("&", 1)[0])
        if page > 8:
            return "<html><body></body></html>"
        return f"""
        <html><body><article data-testid="result-listing">
            <a href="/fahrzeuge/details.html?id={page}">
                <h2 data-testid="listing-title-card-view">Elektroauto {page}</h2>
            </a>
            <span data-testid="price-label">20.000 €</span>
            <div data-testid="listing-details">EZ 01/2023 • 10.000 km • 150 kW (204 PS) • Elektro</div>
        </article></body></html>
        """

    with patch("kfz_crawler.portals.mobile_de.MobileDe._fetch", side_effect=fetch_page):
        portal = MobileDe(max_pages=5)
        listings = portal.search(SearchQuery(name="E-Autos", fuel="elektro"))

    assert len(listings) == 8
    assert listings[-1].raw_id == "8"


def test_mobile_de_location_extraction():
    """PLZ hinter langem Händlernamen darf nicht abgeschnitten werden."""
    E = MobileDe._extract_location
    assert E("Sehr langer Autohaus-Name GmbH & Co. KG Vertragshändler DE-68766 Hockenheim") == "68766 Hockenheim"
    # Kilometer-Falle: 5-stellige km-Zahl darf NICHT als PLZ gelten.
    assert E("Golf · 12.345 km · 90 kW · Privat 70173 Stuttgart") == "70173 Stuttgart"
    # Ohne PLZ: Fallback auf Stadttext (keine Distanz, aber Anzeige bleibt).
    assert E("Nur Berlin ohne PLZ") == "Nur Berlin ohne PLZ"


def test_mobile_de_parser_accepts_layout_variants_and_fallback_fields():
    html = """
    <article class="listing-card">
        <a href="/auto-inserat/car/12345678.html">
            <h3>Volkswagen ID.4 Pro Performance</h3>
        </a>
        <div class="price">27.490 €</div>
        <div class="details">Erstzulassung: 02/2022 · 73 835 km · 110 kW · Vollelektrisch</div>
        <div class="seller">Autohaus Beispiel, DE-66111 Saarbrücken</div>
    </article>
    """

    listing = MobileDe()._parse_cards(html)[0]

    assert listing.raw_id == "12345678"
    assert listing.title == "Volkswagen ID.4 Pro Performance"
    assert listing.price == 27490
    assert listing.year == 2022
    assert listing.mileage == 73835
    assert listing.power_ps == 150
    assert listing.fuel == "elektro"
    assert listing.location == "66111 Saarbrücken"


def test_mobile_de_preserves_results_when_later_page_is_blocked():
    def fetch_page(url):
        page = int(url.split("pageNumber=")[1].split("&", 1)[0])
        if page == 2:
            raise RuntimeError("HTTP 403")
        return SAMPLE_MOBILE_HTML

    with patch("kfz_crawler.portals.mobile_de.MobileDe._fetch", side_effect=fetch_page):
        with pytest.raises(PortalPartialError) as raised:
            MobileDe(max_pages=5).search(SearchQuery(name="Teilabruf"))

    assert raised.value.failed_page == 2
    assert len(raised.value.listings) == 1
    assert raised.value.listings[0].raw_id == "11223344"
