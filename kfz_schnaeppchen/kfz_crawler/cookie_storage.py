"""Persistenter Cookie-Speicher für mobile.de (Akamai-Bypass)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

COOKIE_FILE = Path("/data/mobile_cookies.json")
# Lokaler Fallback für Entwicklungsumgebung
if not Path("/data").exists():
    COOKIE_FILE = Path(__file__).parent.parent / "mobile_cookies.json"


def save_mobile_cookies(raw_cookies: str | dict) -> Dict[str, str]:
    """Parst und speichert Cookie-Header oder Cookie-Dictionary."""
    cookie_dict: Dict[str, str] = {}
    
    if isinstance(raw_cookies, dict):
        cookie_dict = {str(k).strip(): str(v).strip() for k, v in raw_cookies.items()}
    elif isinstance(raw_cookies, str):
        # Format: "name1=val1; name2=val2"
        for part in raw_cookies.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookie_dict[k.strip()] = v.strip()
                
    if not cookie_dict:
        return {}

    data = {
        "updated_at": time.time(),
        "cookies": cookie_dict,
    }
    
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("mobile.de Cookies erfolgreich gespeichert (%d Cookies)", len(cookie_dict))
    except Exception as e:
        logger.error("Fehler beim Speichern der mobile.de Cookies: %s", e)
        
    return cookie_dict


def get_mobile_cookies(max_age_seconds: Optional[float] = None) -> Dict[str, str]:
    """Liefert die gespeicherten mobile.de Cookies oder ein leeres Dict."""
    if not COOKIE_FILE.exists():
        return {}
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        updated = data.get("updated_at")
        if max_age_seconds is not None and (
            not updated or time.time() - float(updated) > max_age_seconds
        ):
            return {}
        return data.get("cookies", {})
    except Exception as e:
        logger.debug("Konnte mobile.de Cookies nicht lesen: %s", e)
        return {}


# Ohne laufenden Browser veraltet die Sitzung. Aelter als das hier wird sie
# nicht mehr benutzt - mobile.de weist sie ohnehin ab, und ein abgelaufenes
# Cookie sieht wie ein Sperrversuch aus.
COOKIE_MAX_ALTER = 12 * 3600


def get_mobile_cookies_status() -> dict:
    """Gibt Statusinformationen zu den gespeicherten Cookies zurück."""
    leer = {"has_cookies": False, "count": 0, "updated_at": None,
            "age_seconds": None, "max_age_seconds": COOKIE_MAX_ALTER,
            "is_fresh": False, "expires_in_seconds": None}
    if not COOKIE_FILE.exists():
        return leer
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        cookies = data.get("cookies", {})
        updated = data.get("updated_at")
        age = round(time.time() - updated, 1) if updated else None
        vorhanden = bool(cookies and ("_abck" in cookies or "bm_sz" in cookies
                                      or len(cookies) > 2))
        frisch = bool(vorhanden and age is not None and age <= COOKIE_MAX_ALTER)
        return {
            "has_cookies": vorhanden,
            "count": len(cookies),
            "updated_at": updated,
            "age_seconds": age,
            "has_abck": "_abck" in cookies,
            "max_age_seconds": COOKIE_MAX_ALTER,
            "is_fresh": frisch,
            "expires_in_seconds": (round(COOKIE_MAX_ALTER - age) if frisch else 0),
        }
    except Exception:
        return leer
