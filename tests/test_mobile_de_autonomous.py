import pytest
from unittest.mock import patch
from kfz_crawler.models import SearchQuery
from kfz_crawler.portals.mobile_de import MobileDe

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


def test_mobile_de_location_extraction():
    """PLZ hinter langem Händlernamen darf nicht abgeschnitten werden."""
    E = MobileDe._extract_location
    assert E("Sehr langer Autohaus-Name GmbH & Co. KG Vertragshändler DE-68766 Hockenheim") == "68766 Hockenheim"
    # Kilometer-Falle: 5-stellige km-Zahl darf NICHT als PLZ gelten.
    assert E("Golf · 12.345 km · 90 kW · Privat 70173 Stuttgart") == "70173 Stuttgart"
    # Ohne PLZ: Fallback auf Stadttext (keine Distanz, aber Anzeige bleibt).
    assert E("Nur Berlin ohne PLZ") == "Nur Berlin ohne PLZ"
