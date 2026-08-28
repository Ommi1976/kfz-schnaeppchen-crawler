"""Tor Service Manager & Circuit Rotation für dynamische IPs."""

from __future__ import annotations

import logging
import socket
import time
from typing import Optional

logger = logging.getLogger(__name__)

TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051


def is_tor_available(host: str = "127.0.0.1", port: int = TOR_SOCKS_PORT, timeout: float = 1.5) -> bool:
    """Prüft, ob der lokale Tor SOCKS5-Proxy erreichbar ist."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def renew_tor_identity(host: str = "127.0.0.1", control_port: int = TOR_CONTROL_PORT, timeout: float = 3.0) -> bool:
    """Sendet SIGNAL NEWNYM an den Tor Control-Port, um eine neue IP / Circuit zu erhalten."""
    try:
        with socket.create_connection((host, control_port), timeout=timeout) as s:
            s.sendall(b'AUTHENTICATE ""\r\n')
            resp = s.recv(1024).decode("utf-8", errors="ignore")
            if "250" not in resp:
                logger.warning("Tor Authentifizierung fehlgeschlagen: %s", resp.strip())
                return False

            s.sendall(b"SIGNAL NEWNYM\r\n")
            resp = s.recv(1024).decode("utf-8", errors="ignore")
            if "250" in resp:
                logger.info("Tor: Neue IP-Identität (Circuit) erfolgreich angefordert.")
                time.sleep(2.0)  # Kurze Wartezeit zum Aufbau des neuen Circuits
                return True
            else:
                logger.warning("Tor SIGNAL NEWNYM fehlgeschlagen: %s", resp.strip())
                return False
    except Exception as e:
        logger.debug("Tor ControlPort nicht erreichbar (%s): %s", control_port, e)
        return False
