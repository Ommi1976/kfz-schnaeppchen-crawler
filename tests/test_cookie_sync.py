from kfz_crawler.cookie_storage import save_mobile_cookies, get_mobile_cookies, get_mobile_cookies_status

def test_save_and_get_mobile_cookies(tmp_path, monkeypatch):
    import kfz_crawler.cookie_storage as cs
    test_file = tmp_path / "test_cookies.json"
    monkeypatch.setattr(cs, "COOKIE_FILE", test_file)
    
    # 1. Speichern als String
    raw = "_abck=test1234; bm_sz=5678; other=abc"
    saved = save_mobile_cookies(raw)
    assert saved["_abck"] == "test1234"
    assert saved["bm_sz"] == "5678"
    
    # 2. Auslesen
    loaded = get_mobile_cookies()
    assert loaded["_abck"] == "test1234"
    
    # 3. Status prüfen
    st = get_mobile_cookies_status()
    assert st["has_cookies"] is True
    assert st["has_abck"] is True
    assert st["count"] == 3

def test_cookie_status_meldet_frische_und_restlaufzeit(tmp_path, monkeypatch):
    """Ohne frisches Cookie liefert mobile.de nichts – das gehört sichtbar."""
    import json, time
    from kfz_crawler import cookie_storage as cs

    datei = tmp_path / "mobile_cookies.json"
    monkeypatch.setattr(cs, "COOKIE_FILE", datei)

    # 1. Noch nie eins empfangen
    status = cs.get_mobile_cookies_status()
    assert status["has_cookies"] is False
    assert status["is_fresh"] is False
    assert status["max_age_seconds"] == cs.COOKIE_MAX_ALTER

    # 2. Frisch übertragen
    cookies = {"_abck": "x", "bm_sz": "y", "sid": "z"}
    datei.write_text(json.dumps(
        {"cookies": cookies, "updated_at": time.time() - 3600}), encoding="utf-8")
    status = cs.get_mobile_cookies_status()
    assert status["is_fresh"] is True
    assert status["has_abck"] is True
    assert status["count"] == 3
    # Nach einer Stunde bleiben elf der zwölf.
    assert 10.5 * 3600 < status["expires_in_seconds"] <= 11 * 3600

    # 3. Zu alt: die Sitzung veraltet ohne laufenden Browser
    datei.write_text(json.dumps(
        {"cookies": cookies, "updated_at": time.time() - (cs.COOKIE_MAX_ALTER + 60)}),
        encoding="utf-8")
    status = cs.get_mobile_cookies_status()
    assert status["has_cookies"] is True
    assert status["is_fresh"] is False
    assert status["expires_in_seconds"] == 0
