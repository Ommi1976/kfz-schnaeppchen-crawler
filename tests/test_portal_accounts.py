"""No portal traffic: privacy, session ownership and shared-profile regressions."""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kfz_crawler import portal_accounts as accounts
from kfz_crawler.mobile_runtime import MobileBrowser, MobileDeferred, STATE_KEY, load_state, save_state
from kfz_crawler.storage import SeenStore


@pytest.fixture
def store():
    db = SeenStore(":memory:")
    yield db
    db.close()


@pytest.mark.parametrize("url", ["http://mobile.de/", "https://mobile.de.evil.test/",
    "https://mobile.de@127.0.0.1/", "https://localhost/", "file:///etc/passwd",
    "https://suchen.mobile.de:444/", "https://suchen.mobile.de:bad/"])
def test_no_arbitrary_browser_navigation(url):
    assert not accounts.allowed_navigation("mobile_de", url)


def test_login_domains_are_scoped_to_the_selected_portal():
    assert accounts.allowed_navigation("mobile_de", "https://login.mobile.de/")
    assert accounts.allowed_navigation("autoscout24", "https://accounts.autoscout24.com/")
    assert not accounts.allowed_navigation("mobile_de", "https://www.autouncle.de/")


def test_mobile_uses_official_buyer_login_not_search_management():
    from urllib.parse import urlparse, parse_qs
    url = accounts.PORTALS["mobile_de"]["url"]
    parsed = urlparse(url)
    assert parsed.hostname == "www.mobile.de"
    assert parsed.path == "/api/auth/login"
    assert parse_qs(parsed.query)["source_uri"] == ["https://www.mobile.de/"]
    assert "state" not in parse_qs(parsed.query) and "nonce" not in parse_qs(parsed.query)
    assert accounts.allowed_navigation("mobile_de", "https://id.mobile.de/login")


@pytest.mark.parametrize("html,expected", [
    ("<script>Abmelden</script><p>Cookie gespeichert</p>", "unverified"),
    ("<div hidden><button>Abmelden</button></div><button>Anmelden</button>", "anonymous"),
    ("<p>Zum Beenden auf Abmelden klicken.</p>", "unverified"),
    ("<button>Abmelden</button>", "authenticated"),
    ("<input type='password'>", "anonymous"),
    ("<h1>Zugriff verweigert</h1><p>Bitte anmelden</p>", "blocked"),
    ("<h1>Access denied</h1><p>CAPTCHA</p>", "blocked"),
    ("<h1>Bitte CAPTCHA bestätigen</h1>", "verification_required"),
    ("<h1>Unusual traffic</h1><p>Complete CAPTCHA</p>", "verification_required"),
    ("<div hidden>Zugriff verweigert</div><input type='password'>", "anonymous"),
    ("<script>captcha</script><input type='password'>", "anonymous"),
])
def test_login_requires_explicit_evidence(html, expected):
    assert accounts.auth_evidence(html) == expected


def test_session_owner_and_expiry():
    worker = SimpleNamespace(_account_session={"id": "s", "owner": "alice", "expires_at": time.time()+60}, _close=Mock())
    for sid, who in [("s", "bob"), ("other", "alice")]:
        with pytest.raises(accounts.AccountError) as e:
            accounts._require_session(worker, sid, who)
        assert e.value.status == 403
    assert accounts._require_session(worker, "s", "alice")["id"] == "s"
    worker._account_session["expires_at"] = 0
    with pytest.raises(accounts.AccountError) as e:
        accounts._require_session(worker, "s", "alice")
    assert e.value.status == 410


def test_automation_cannot_navigate_during_login(store, monkeypatch):
    worker = MobileBrowser()
    try:
        worker._account_session = {"expires_at": time.time()+60}
        opener = Mock()
        monkeypatch.setattr(worker, "_open", opener)
        with pytest.raises(MobileDeferred):
            worker.fetch("https://suchen.mobile.de/fahrzeuge/search.html", store=store)
        with pytest.raises(MobileDeferred):
            worker.fetch_image("https://img.classistatic.de/photo.jpg", store=store)
        opener.assert_not_called()
        assert load_state(store, STATE_KEY) == {}
    finally:
        worker.close()


def test_observation_does_not_release_search_pause(store):
    breaker = {"blocked_until": time.time()+7200, "status": "blocked", "block_count": 3}
    save_state(store, STATE_KEY, breaker)
    save_state(store, accounts.account_key("mobile_de"), {"enabled": True})
    page = Mock()
    page.locator.return_value.evaluate_all.return_value = [{"text": "Abmelden", "password": False}]
    page.content.return_value = "<button>Abmelden</button>"
    assert accounts._observe(store, "mobile_de", page) == "authenticated"
    assert load_state(store, STATE_KEY) == breaker
    status = accounts.account_status(store, "mobile_de")
    assert status["auth_state"] == "authenticated"
    assert status["search_status"] == "blocked"
    assert status["last_search_success"] is None


def test_outdated_login_not_claimed_as_current(store):
    save_state(store, accounts.account_key("mobile_de"), {
        "enabled": True, "auth_state": "authenticated", "checked_at": 1})
    assert accounts.account_status(store, "mobile_de")["auth_state"] == "unverified"


def test_hard_denial_snapshot_and_status_never_claim_verification(store):
    save_state(store, accounts.account_key("mobile_de"), {"enabled": True})
    page = Mock()
    page.is_closed.return_value = False
    page.url = "https://id.mobile.de/login"
    page.locator.return_value.evaluate_all.return_value = []
    page.content.return_value = "<h1>Zugriff verweigert</h1>"
    page.screenshot.return_value = b"synthetic-image"
    page.viewport_size = {"width": 1440, "height": 900}  # Firefox: emulated viewport
    worker = SimpleNamespace(_account_session={"id": "s", "owner": "o", "expires_at": time.time()+60},
                             _context=SimpleNamespace(pages=[page]))
    snapshot = accounts._snapshot(worker, "mobile_de", store, "s", "o")
    assert snapshot["auth_state"] == "blocked"
    assert snapshot["message"] == accounts.BLOCKED_LOGIN_MESSAGE
    assert accounts.account_status(store, "mobile_de")["auth_state"] == "blocked"
    assert accounts.account_status(store, "mobile_de")["message"] == accounts.BLOCKED_LOGIN_MESSAGE


@pytest.mark.parametrize("action", ["text", "key", "click", "scroll"])
def test_denied_page_never_receives_credentials_or_input(store, action):
    page = Mock()
    page.is_closed.return_value = False
    page.url = "https://id.mobile.de/login"
    page.content.return_value = "<h1>Zugriff verweigert</h1>"
    worker = SimpleNamespace(_account_session={"id": "s", "owner": "o", "expires_at": time.time()+60},
                             _context=SimpleNamespace(pages=[page]))
    with pytest.raises(accounts.AccountError):
        accounts._input(worker, "mobile_de", store, {"session_id": "s", "action": action, "text": "private-value"}, "o")
    assert not page.keyboard.mock_calls and not page.mouse.mock_calls


def test_disconnect_removes_only_profile_and_keeps_pause(store, tmp_path, monkeypatch):
    profile = tmp_path / "firefox_profile"
    profile.mkdir()
    (profile / "synthetic-cookie").touch()
    unrelated = tmp_path / "keep"
    unrelated.touch()
    save_state(store, STATE_KEY, {"blocked_until": 9999999999})
    worker = SimpleNamespace(_profile_dir=profile, _account_session=None, _close=Mock())
    monkeypatch.setattr(accounts, "profile_path", lambda key: profile)
    accounts._disconnect(worker, "mobile_de", store)
    assert not profile.exists() and unrelated.exists()
    assert load_state(store, STATE_KEY)["blocked_until"] == 9999999999
    assert not accounts.account_enabled(store, "mobile_de")


def test_disconnect_rejects_broad_directory(store, tmp_path, monkeypatch):
    worker = SimpleNamespace(_profile_dir=tmp_path, _account_session=None, _close=Mock())
    monkeypatch.setattr(accounts, "profile_path", lambda key: tmp_path)
    with pytest.raises(accounts.AccountError):
        accounts._disconnect(worker, "mobile_de", store)
    assert tmp_path.exists()


def test_broken_page_does_not_prevent_closing_login(store, monkeypatch):
    worker = SimpleNamespace(_account_session={"id":"s", "owner":"o", "expires_at":time.time()+60}, _close=Mock())
    monkeypatch.setattr(accounts, "_login_page", Mock(side_effect=RuntimeError("broken")))
    assert accounts._close_login(worker, "mobile_de", store, "s", "o") == {"ok":True}
    assert worker._account_session is None
    worker._close.assert_called_once()


def test_linked_requests_use_the_shared_worker(store, monkeypatch):
    save_state(store, accounts.account_key("autoscout24"), {"enabled": True})
    worker = Mock(); worker.fetch.return_value = "<html>from shared profile</html>"
    factory = Mock(return_value=worker)
    monkeypatch.setattr(accounts, "portal_browser", factory)
    from kfz_crawler.portals.autoscout24 import AutoScout24
    portal = AutoScout24(); portal.store = store
    assert portal._get("https://www.autoscout24.de/lst").text == worker.fetch.return_value
    factory.assert_called_once_with("autoscout24")


def test_browser_errors_do_not_echo_credentials(store, monkeypatch):
    worker = MobileBrowser()
    try:
        monkeypatch.setattr(accounts, "portal_browser", lambda key: worker)
        monkeypatch.setattr(accounts, "_input", Mock(side_effect=RuntimeError("password=private-value")))
        with pytest.raises(accounts.AccountError) as e:
            accounts.account_action("mobile_de", "input", store, "owner", payload={})
        assert "private-value" not in str(e.value)
    finally:
        worker.close()


def test_login_view_reports_real_browser_size(store):
    # Chrome on the GPU compositor has no emulated viewport.
    worker = SimpleNamespace(_account_session={"id": "s", "owner": "alice", "expires_at": time.time()+60})
    page = Mock(url="https://www.mobile.de/", viewport_size=None)
    page.evaluate.return_value = {"width": 1280, "height": 577}
    page.screenshot.return_value = b"jpeg"
    page.locator.return_value.evaluate_all.return_value = []
    page.content.return_value = "<p>Start</p>"
    worker._context = SimpleNamespace(pages=[page])
    worker._page = page
    view = accounts._snapshot(worker, "mobile_de", store, "s", "alice")
    assert (view["width"], view["height"]) == (1280, 577)
    page.keyboard = Mock()
    page.mouse = Mock()
    with pytest.raises(accounts.AccountError):
        accounts._input(worker, "mobile_de", store, {"session_id": "s", "action": "click", "x": 1300, "y": 10}, "alice")
    worker._account_session["last_input"] = 0  # input throttle
    accounts._input(worker, "mobile_de", store, {"session_id": "s", "action": "click", "x": 1279, "y": 576}, "alice")
    page.mouse.click.assert_called_once_with(1279, 576)
