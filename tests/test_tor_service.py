from kfz_crawler.tor_service import is_tor_available, renew_tor_identity

def test_tor_service_functions():
    # Prüft Basisfunktionalität ohne aktiven Tor-Daemon (liefert sicher False)
    assert isinstance(is_tor_available(port=9999), bool)
    assert is_tor_available(port=9999) is False
    assert renew_tor_identity(control_port=9999) is False
