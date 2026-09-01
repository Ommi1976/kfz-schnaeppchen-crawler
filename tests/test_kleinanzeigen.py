"""Kleinanzeigen-Parser: strukturell statt über Stilklassen."""
from kfz_crawler.models import SearchQuery
from kfz_crawler.portals.kleinanzeigen import Kleinanzeigen

# Neues Markup: Utility-Klassen, keine .aditem-Struktur mehr.
NEUES_MARKUP = """
<html><body>
<article class="flex justify-between p-medium">
  <a href="/s-anzeige/dacia-spring-electric-45-ps/3499617433-216-364">
    <h2>Dacia Spring Electric 45 PS | 5.389 km | 1. Hand</h2>
  </a>
  <img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/f1/abc.jpg">
  <div>12 TOP 66679 Losheim am See Heute Dacia Spring Electric 45 PS | 5.389 km
       EZ 03/2023 12.599 &euro; VB</div>
</article>
<article class="flex justify-between p-medium">
  <a href="/s-anzeige/autoankauf-sofort-geld/3384940548-216-4708"><h2>AUTOANKAUF Sofort Geld</h2></a>
  <div>4 TOP 35745 Herborn Gesuch 999 &euro; VB</div>
</article>
</body></html>
"""


def test_parser_kommt_ohne_stilklassen_aus():
    """Kleinanzeigen ist auf Tailwind umgestellt – .aditem gibt es nicht mehr."""
    portal = Kleinanzeigen(max_pages=1)
    treffer = portal._parse(NEUES_MARKUP, SearchQuery(name="EV"))

    assert len(treffer) == 1                      # das Gesuch fällt raus
    l = treffer[0]
    assert l.price == 12599
    assert l.mileage == 5389
    assert l.year == 2023
    assert l.location == "66679 Losheim am See"   # ohne Datum, ohne Titelwörter
    assert l.raw_id == "3499617433"
    assert "Dacia Spring" in l.title


def test_preis_erkennung_verwechselt_km_nicht_mit_euro():
    """"5.389 km" ist kein Preis – und "999 € VB" muss trotzdem greifen."""
    assert Kleinanzeigen._PREIS_RE.search("5.389 km") is None
    assert Kleinanzeigen._PREIS_RE.search("999 € VB").group(1) == "999"
    assert Kleinanzeigen._PREIS_RE.search("Preis 27.500 EUR").group(1) == "27.500"


def test_ort_wird_vom_einstelldatum_getrennt():
    assert Kleinanzeigen._ort_saeubern("Nürtingen Heute") == "Nürtingen"
    assert Kleinanzeigen._ort_saeubern("Losheim am See") == "Losheim am See"
    assert Kleinanzeigen._ort_saeubern("Eisenach 12.03.2026") == "Eisenach"
