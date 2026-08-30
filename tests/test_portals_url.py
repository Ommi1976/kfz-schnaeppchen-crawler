import pytest
from kfz_crawler.models import SearchQuery
from kfz_crawler.portals.autoscout24 import AutoScout24
from kfz_crawler.portals.kleinanzeigen import Kleinanzeigen
from kfz_crawler.portals.mobile_de import MobileDe
from kfz_crawler.portals.autouncle import AutoUncle
from kfz_crawler.portals.heycar import Heycar


def test_autoscout24_url_builder():
    p = AutoScout24()
    q = SearchQuery(
        name="Test",
        make="audi",
        model="a4",
        price_from=10000,
        price_to=25000,
        year_from=2018,
        year_to=2022,
        mileage_from=10000,
        mileage_to=100000,
        fuel="diesel",
        transmission="automatik",
        seller="haendler",
        power_from=150,
        power_to=200,
        zip_code="66111",
        radius_km=50,
        equipment=[1, 2],
    )
    url = p._build_url(q, page=1)
    assert "/lst/audi/a4?" in url
    assert "pricefrom=10000" in url
    assert "priceto=25000" in url
    assert "fregfrom=2018" in url
    assert "fregto=2022" in url
    assert "kmfrom=10000" in url
    assert "kmto=100000" in url
    assert "fuel=D" in url
    assert "gear=A" in url
    assert "customertype=D" in url
    assert "powertype=kw" in url
    assert "zip=66111" in url
    assert "zipradius=50" in url
    assert "eq=1,2" in url


def test_kleinanzeigen_url_builder():
    p = Kleinanzeigen()
    q = SearchQuery(
        name="Test",
        make="volkswagen",
        model="golf",
        price_from=5000,
        price_to=15000,
        zip_code="66111",
        radius_km=30,
    )
    url = p._build_url(q, page=1)
    assert "/s-autos/66111/preis:5000:15000/volkswagen-golf/k0c216?radius=30" in url


def test_mobile_de_url_builder():
    p = MobileDe()
    q = SearchQuery(
        name="Test",
        make="bmw",
        model="320",
        price_from=15000,
        price_to=30000,
        year_from=2019,
        mileage_to=80000,
        fuel="benzin",
        zip_code="10115",
        radius_km=100,
        ev_range_from=300,
        equipment=[34, 133],
    )
    url = p._build_url(q, page=1)
    assert "suchen.mobile.de/fahrzeuge/search.html?" in url
    assert "p=15000%3A30000" in url or "p=15000:30000" in url
    assert "fr=2019%3A" in url or "fr=2019:" in url
    assert "ml=%3A80000" in url or "ml=:80000" in url
    assert "ft=PETROL" in url
    assert "ambc=10115" in url
    assert "rad=100" in url
    assert "q=bmw+320" in url
    assert "fe=ELECTRIC_HEATED_SEATS" in url
    assert "fe=ADAPTIVE_CRUISE_CONTROL" in url


def test_autouncle_url_builder():
    p = AutoUncle()
    q = SearchQuery(name="Test", make="ford", model="focus")
    url = p._build_url(q, page=2)
    assert "/de/gebrauchtwagen/ford/focus?page=2" in url

    ev_url = p._build_url(SearchQuery(name="EV", fuel="elektro"), page=1)
    assert "/de/gebrauchtwagen/f-elektro?page=1" in ev_url


def test_heycar_url_builder():
    p = Heycar()
    q = SearchQuery(name="Test", make="skoda", model="octavia", price_to=20000)
    url = p._build_url(q, page=1)
    assert "hey.car/gebrauchtwagen?" in url
    assert "makes=skoda" in url
    assert "models=octavia" in url
    assert "priceTo=20000" in url
