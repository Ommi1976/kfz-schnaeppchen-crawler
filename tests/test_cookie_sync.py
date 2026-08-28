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