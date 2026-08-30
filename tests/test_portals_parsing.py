import json
import pytest
from bs4 import BeautifulSoup
from kfz_crawler.models import Listing, SearchQuery
from kfz_crawler.portals.autoscout24 import AutoScout24
from kfz_crawler.portals.kleinanzeigen import Kleinanzeigen
from kfz_crawler.portals.mobile_de import MobileDe
from kfz_crawler.portals.autouncle import AutoUncle


def test_autoscout24_parse_next_data():
    sample_json = {
        "props": {
            "pageProps": {
                "listings": [
                    {
                        "id": "123456",
                        "url": "/angebote/audi-a4-diesel-123456",
                        "vehicle": {
                            "make": "Audi",
                            "model": "A4",
                            "modelVersionInput": "2.0 TDI Avant",
                            "fuelType": "Diesel",
                            "transmissionType": "Automatik",
                            "rawPowerInKw": 110,
                            "bodyType": "Kombi",
                        },
                        "tracking": {
                            "price": 18500,
                            "mileage": 75000,
                            "firstRegistration": "2019-05-01",
                        },
                        "location": {"city": "Saarbrücken"},
                    }
                ]
            }
        }
    }
    p = AutoScout24()
    listings = p._parse_next_data(sample_json)
    assert len(listings) == 1
    l = listings[0]
    assert l.portal == "AutoScout24"
    assert "Audi A4" in l.title
    assert l.price == 18500
    assert l.year == 2019
    assert l.mileage == 75000
    assert l.fuel == "Diesel"
    assert l.power_ps == 150
    assert l.transmission == "automatik"
    assert l.location == "Saarbrücken"


def test_kleinanzeigen_parse_html():
    sample_html = """
    <div class="ad-list">
        <article class="aditem" data-adid="99887766">
            <div class="aditem-main--top--left">66111 Saarbrücken</div>
            <div class="aditem-main--middle--price-shipping--price">14.900 €</div>
            <h2 class="aditem-main--middle--title"><a href="/s-anzeige/vw-golf-7-tdi/99887766">VW Golf 7 VII TDI DSG Highline</a></h2>
            <p class="aditem-main--middle--description">EZ 2019, 85.000 km, sehr gepflegt, 150 PS, Diesel, Automatik</p>
        </article>
    </div>
    """
    p = Kleinanzeigen()
    q = SearchQuery(name="Golf", make="volkswagen", model="golf")
    listings = p._parse(sample_html, q)
    assert len(listings) == 1
    l = listings[0]
    assert l.portal == "Kleinanzeigen"
    assert l.price == 14900
    assert l.year == 2019
    assert l.mileage == 85000
    assert l.location == "66111 Saarbrücken"


def test_kleinanzeigen_parse_detail():
    detail_html = """
    <div>
        <ul class="addetailslist">
            <li class="addetailslist--detail">
                <span class="addetailslist--detail--value">150 PS</span> Leistung
            </li>
            <li class="addetailslist--detail">
                <span class="addetailslist--detail--value">Automatik</span> Getriebe
            </li>
            <li class="addetailslist--detail">
                <span class="addetailslist--detail--value">Diesel</span> Kraftstoff
            </li>
            <li class="addetailslist--detail">
                <span class="addetailslist--detail--value">Beschädigtes Fahrzeug</span> Fahrzeugzustand
            </li>
        </ul>
        <div class="description">Batteriekapazität ca 58 kWh bei Elektrofahrzeugen</div>
    </div>
    """
    p = Kleinanzeigen()
    l = Listing(portal="Kleinanzeigen", title="Golf", url="http://x")
    p._parse_detail(detail_html, l)
    assert l.power_ps == 150
    assert l.transmission == "automatik"
    assert l.fuel == "diesel"
    assert "Beschädigtes Fahrzeug" in (l.body or "")
    assert "Batteriekapazität ca 58 kWh" in (l.body or "")
    assert l.battery_kwh == 58.0


def test_mobile_de_parse_details():
    text = "Unfallfrei • EZ 03/2020 • 65.000 km • 110 kW (150 PS) • Diesel"
    det = MobileDe._parse_details(text)
    assert det["year"] == 2020
    assert det["mileage"] == 65000
    assert det["power_ps"] == 150
    assert det["fuel"] == "diesel"
    assert det["damaged"] is False

    text_damaged = "Unfallfahrzeug • EZ 05/2018 • 120.000 km • 85 kW (116 PS) • Benzin"
    det_dam = MobileDe._parse_details(text_damaged)
    assert det_dam["year"] == 2018
    assert det_dam["damaged"] is True


def test_autouncle_parse_rendered_card_normalizes_ev_fields_and_offer_url():
    sample_html = """
    <article>
      <a href="/de/d/226093647-gebraucht-2024-ford-explorer-286-ps">
        <h3>Gebraucht (2024) Ford Explorer 286 PS | Guter Preis</h3>
      </a>
      <p>EV 77 kWh RWD AHK Fahrerass.-Paket</p>
      <ul>
        <li>Aug 2024</li><li>6.700 km</li>
        <li>E-Auto (elektro)</li><li>602 km Reichweite</li>
        <li>210 kW (286 PS)</li>
      </ul>
      <span>38.878 €</span>
      <span>Unter dem Marktpreis 2.122 €</span>
      <a href="/de/das_wiedersehen/autohausmoeller-de/226093647/398863180">Zum Angebot</a>
      <div>99817 Eisenach, Thüringen</div>
    </article>
    """
    listings = AutoUncle()._parse(sample_html)
    assert len(listings) == 1
    listing = listings[0]
    assert listing.raw_id == "226093647"
    assert listing.url.endswith("/398863180")
    assert listing.price == 38878
    assert listing.year == 2024
    assert listing.mileage == 6700
    assert listing.power_ps == 286
    assert listing.ev_range_km == 602
    assert listing.battery_kwh == 77.0
    assert listing.location == "99817 Eisenach"
