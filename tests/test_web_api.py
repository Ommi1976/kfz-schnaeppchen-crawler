import pytest
from fastapi.testclient import TestClient
from kfz_crawler.web import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Temporäre DB für die Tests verwenden
    test_db = tmp_path / "test_seen.db"
    monkeypatch.setenv("KFZ_DB_PATH", str(test_db))
    with TestClient(app) as c:
        # Die Oberfläche läuft über den Home-Assistant-Ingress; dieser Header
        # kennzeichnet den Weg. Direktzugriffe ohne ihn brauchen ein Token.
        c.headers.update({"X-Ingress-Path": "/api/hassio_ingress/test"})
        yield c


def test_api_ready(client):
    r = client.get("/api/ready")
    assert r.status_code == 200
    assert r.json() == {"ready": True}


def test_api_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    meta = r.json()
    assert "portals" in meta
    assert "fuel" in meta
    assert "autoscout24" in meta["portals"]


def test_api_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    status = r.json()
    assert "version" in status
    assert "running" in status
    assert "searches" in status


def test_api_searches_lifecycle(client):
    # 1. Erstellen
    payload = {
        "name": "BMW 320d Touring",
        "make": "bmw",
        "model": "320",
        "fuel": "diesel",
        "price_to": 25000,
        "zip_code": "66111",
        "radius_km": 50,
        "active": True,
    }
    r = client.post("/api/searches", json=payload)
    assert r.status_code == 201
    created = r.json()
    assert created["id"] is not None
    assert created["name"] == "BMW 320d Touring"
    assert created["zip_code"] == "66111"
    assert created["radius_km"] == 50
    sid = created["id"]

    # 2. Auflisten
    r_list = client.get("/api/searches")
    assert r_list.status_code == 200
    searches = r_list.json()
    assert any(s["id"] == sid for s in searches)

    # 3. Aktualisieren
    payload["name"] = "BMW 320d Touring M-Sport"
    payload["price_to"] = 28000
    r_up = client.put(f"/api/searches/{sid}", json=payload)
    assert r_up.status_code == 200
    assert r_up.json()["name"] == "BMW 320d Touring M-Sport"
    assert r_up.json()["price_to"] == 28000

    # 4. Löschen
    r_del = client.delete(f"/api/searches/{sid}")
    assert r_del.status_code == 204

    # 5. Prüfen ob gelöscht
    r_up_after = client.put(f"/api/searches/{sid}", json=payload)
    assert r_up_after.status_code == 404


def test_direct_access_without_token_is_rejected(client):
    """Über den LAN-Port darf niemand ohne Token schreiben."""
    # Eigener Client ohne Ingress-Header: so sieht ein Zugriff direkt aus dem
    # Heimnetz aus. Die App läuft bereits über die Fixture.
    direkt = TestClient(app)

    r = direkt.post("/api/searches", json={"name": "Fremdzugriff"})
    assert r.status_code == 401

    # Lesen bleibt erlaubt – dort steht nichts Schützenswertes.
    assert direkt.get("/api/ready").status_code == 200

    # Mit gültigem Token geht es.
    token = client.app.state.store.ingest_token()
    r_ok = direkt.post("/api/searches", json={"name": "Mit Token"},
                       headers={"X-KFZ-Token": token})
    assert r_ok.status_code == 201


def test_mobile_cookie_endpoint_requires_token_and_abck(client):
    """Der Cookie-Endpunkt prüft Token und Vollständigkeit der Sitzung."""
    token = client.app.state.store.ingest_token()

    r_ohne = client.post("/api/mobile-cookies", json={"cookies": "a=1; _abck=x"},
                         headers={"X-KFZ-Token": "falsch"})
    assert r_ohne.status_code == 401

    r_unvollstaendig = client.post("/api/mobile-cookies", json={"cookies": "a=1; b=2"},
                                   headers={"X-KFZ-Token": token})
    assert r_unvollstaendig.status_code == 400

    r_ok = client.post("/api/mobile-cookies", json={"cookies": "a=1; _abck=echtaussehend"},
                       headers={"X-KFZ-Token": token})
    assert r_ok.status_code == 200
    assert r_ok.json()["saved_count"] >= 2
