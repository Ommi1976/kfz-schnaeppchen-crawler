"""Detailseiten entscheiden mit: die Trefferkarte ist abgeschnitten.

Gemessen am Bestand lieferte die Karte im Mittel 273 Zeichen und brach mitten
im Satz ab. Hinweise auf Leasing oder Beschädigung stehen dahinter.
"""
from collections import Counter

from kfz_crawler.main import _mit_detailtext_nachpruefen
from kfz_crawler.models import Listing, SearchQuery


class _Einstellungen:
    verify_details = False


class _Cfg:
    settings = _Einstellungen()


class _PortalMitDetailseite:
    """Stellt nach, was der echte Abruf tut: er hängt den Volltext an."""
    name = "Kleinanzeigen"

    def __init__(self, texte):
        self.texte = texte
        self.abgerufen = []

    def enrich(self, listings, query, force=False):
        for l in listings:
            self.abgerufen.append(l.url)
            zusatz = self.texte.get(l.url)
            if zusatz:
                l.body = f"{l.body or ''} {zusatz}"
        return listings


def _inserat(url, titel, body=""):
    return Listing(portal="Kleinanzeigen", url=url, title=titel,
                   price=20000, year=2023, mileage=30000, body=body)


def test_detailseite_entlarvt_leasing_und_schaden():
    treffer = [
        _inserat("https://x/1", "Toyota bZ4X Teamplayer ACC LED"),
        _inserat("https://x/2", "Skoda Enyaq iV 80 Loft"),
        _inserat("https://x/3", "VW ID.4 Pro Performance"),
    ]
    portal = _PortalMitDetailseite({
        "https://x/1": "Fahrzeug aus Leasingübernahme, Restlaufzeit 24 Monate.",
        "https://x/2": "Achtung: Fahrzeug hat einen Unfallschaden hinten links.",
        "https://x/3": "Scheckheftgepflegt, unfallfrei, aus erster Hand.",
    })
    exclusions: Counter = Counter()
    uebrig = _mit_detailtext_nachpruefen(
        portal, treffer, SearchQuery(name="E-Autos"), _Cfg(), None, exclusions
    )
    assert [l.url for l in uebrig] == ["https://x/3"]
    assert any("laut Detailseite" in grund for grund in exclusions)


def test_bereits_geladener_text_wird_nicht_erneut_abgerufen():
    """Sonst kostet jeder Lauf rund hundert Anfragen je Portal."""
    class _Store:
        def detailtexte(self, search_name=None, min_laenge=800):
            return {treffer[0].fingerprint: "Langer gespeicherter Text, unfallfrei."}

    treffer = [_inserat("https://x/1", "VW ID.3 Pro"),
               _inserat("https://x/2", "VW ID.4 Pro")]
    portal = _PortalMitDetailseite({})
    _mit_detailtext_nachpruefen(
        portal, treffer, SearchQuery(name="E-Autos"), _Cfg(), None,
        Counter(), _Store()
    )
    assert portal.abgerufen == ["https://x/2"]
    assert "gespeicherter Text" in treffer[0].body
