from kfz_crawler.models import extract_warranty, infer_listing_details, Listing
from kfz_crawler.geo import parse_location, calculate_distance_km

def test_extract_warranty():
    assert extract_warranty("VW ID.3 mit 8 Jahre / 160.000 km Batterie-Garantie") == "8 Jahre / 160.000 km"
    assert extract_warranty("Batterie-Garantie: bis 11/2030 oder 160000 km") is not None
    assert extract_warranty("Fahrzeug mit 12 Monate Gebrauchtwagengarantie") == "12 Monate Gebrauchtwagengarantie"
    assert extract_warranty("Inklusive Herstellergarantie") == "Herstellergarantie"
    assert extract_warranty("Garantie bis 05/2028") == "Garantie bis 05/2028"
    assert extract_warranty("Batterie-Garantie: Nicht angegeben") is None

def test_parse_location_and_distance():
    zip_code, city = parse_location("DE-94447 Plattling")
    assert zip_code == "94447"
    assert city == "Plattling"
    
    zip_code2, city2 = parse_location("80331 München")
    assert zip_code2 == "80331"
    assert city2 == "München"
    
    dist = calculate_distance_km("94447", "80331")
    assert dist is not None
    assert 100 <= dist <= 180  # Plattling nach München ca. 130 km

def test_infer_listing_details():
    l = Listing(
        portal="mobile.de",
        title="VW ID.3 Pro S 8 Jahre / 160.000 km Garantie",
        url="http://x",
        location="DE-94447 Plattling",
        body="Batterie-Garantie: 8 Jahre / 160.000 km",
    )
    infer_listing_details(l, query_zip="80331")
    assert l.warranty == "8 Jahre / 160.000 km"
    assert l.location_zip == "94447"
    assert l.location_city == "Plattling"
    assert l.distance_km is not None