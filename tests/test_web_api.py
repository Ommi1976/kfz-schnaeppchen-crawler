import pytest
from fastapi.testclient import TestClient
from kfz_crawler.web import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Temporäre DB für die Tests verwenden
    test_db = tmp_path / "test_seen.db"
    monkeypatch.setenv("KFZ_DB_PATH", str(test_db))
    with TestClient(app) as c:
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
    assert "mobile" in status


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


def test_api_mobile_cookies_token_protection(client):
    token = client.app.state.store.get_setting("ingest_token", "")
    assert token != ""

    # Ohne Token -> 401
    r_no_tok = client.post("/api/mobile-cookies", json={"cookies": "foo=bar; _abck=123"})
    assert r_no_tok.status_code == 401

    # Mit falschem Token -> 401
    r_wrong_tok = client.post(
        "/api/mobile-cookies",
        headers={"X-KFZ-Token": "wrong"},
        json={"cookies": "foo=bar; _abck=123"},
    )
    assert r_wrong_tok.status_code == 401

    # Mit ungültigen Cookies (ohne _abck) -> 400
    r_bad_cookie = client.post(
        "/api/mobile-cookies",
        headers={"X-KFZ-Token": token},
        json={"cookies": "session=123"},
    )
    assert r_bad_cookie.status_code == 400
