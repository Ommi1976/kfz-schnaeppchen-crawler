from unittest.mock import patch, MagicMock
from kfz_crawler.models import Listing
from kfz_crawler.battery_analyzer import enrich_listing_battery_deep, extract_soh_from_image_urls, ocr_image_bytes

def test_enrich_listing_battery_deep_from_text():
    l = Listing(
        portal="mobile.de",
        title="BMW i3 120Ah",
        url="http://x",
        body="Fahrzeug in Top Zustand. Aviloo Batteriezertifikat liegt vor: State of Health 95.5%",
    )
    ok = enrich_listing_battery_deep(l)
    assert ok is True
    assert l.battery_soh == 95.5

def test_enrich_listing_battery_deep_from_image_mock():
    l = Listing(
        portal="AS24",
        title="VW ID.3 Pro",
        url="http://x",
        body="Keine Angaben im Text",
    )
    with patch("kfz_crawler.battery_analyzer.extract_soh_from_image_urls", return_value=93.0):
        ok = enrich_listing_battery_deep(l, image_urls=["http://example.com/cert.jpg"])
        assert ok is True
        assert l.battery_soh == 93.0

def test_ocr_fallback_when_empty():
    assert ocr_image_bytes(b"") is None