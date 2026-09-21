import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from kfz_crawler.web import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KFZ_DB_PATH", str(tmp_path / "test.db"))
    async def idle(_app):
        await asyncio.Future()
    monkeypatch.setattr("kfz_crawler.web._scheduler", idle)
    with TestClient(app, client=("172.30.32.2", 1234), headers={"X-Ingress-Path":"/api/hassio_ingress/test"}) as c:
        yield c


def test_all_account_reads_and_writes_protected(client):
    direct = TestClient(app)
    for headers in ({}, {"X-Ingress-Path":"/spoofed"}, {"X-Forwarded-For":"172.30.32.2", "X-Ingress-Path":"/spoofed"}):
        assert direct.get("/api/accounts", headers=headers).status_code == 401
        assert direct.get("/api/accounts/mobile_de/session?session_id=fake", headers=headers).status_code == 401
        assert direct.post("/api/accounts/mobile_de/connect", headers=headers, json={}).status_code == 401
        assert direct.post("/api/searches", headers=headers, json={"name":"spoof"}).status_code == 401


def test_trusted_ingress_and_token_private_status(client):
    response = client.get("/api/accounts")
    assert response.status_code == 200
    assert len(response.json()["portals"]) == 4
    assert "no-store" in response.headers["cache-control"]
    direct = TestClient(app)
    token = app.state.store.ingest_token()
    response = direct.get("/api/accounts", headers={"X-KFZ-Token":token})
    assert response.status_code == 200 and token not in response.text
    assert "cookies" not in response.text and "password" not in response.text


def test_cross_site_and_bad_portals_rejected(client):
    assert client.post("/api/accounts/mobile_de/connect", json={}, headers={"Sec-Fetch-Site":"cross-site"}).status_code == 403
    assert client.post("/api/accounts/unknown/connect", json={}).status_code == 404


def test_no_browser_without_authorization_and_owner_passed(client, monkeypatch):
    handler = Mock(return_value={"ok":True})
    monkeypatch.setattr("kfz_crawler.accounts_api.account_action", handler)
    direct = TestClient(app)
    assert direct.post("/api/accounts/mobile_de/input", json={}).status_code == 401
    handler.assert_not_called()
    assert client.post("/api/accounts/mobile_de/input", json={"text":"synthetic"}).status_code == 200
    call = handler.call_args
    assert len(call.args[3]) == 64
    assert call.kwargs["payload"]["text"] == "synthetic"
