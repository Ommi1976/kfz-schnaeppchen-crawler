"""Laden und Validieren der Konfiguration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from .models import SearchQuery


@dataclass
class Settings:
    deal_threshold: float = 0.15
    min_comparables: int = 5
    request_delay: float = 2.5
    max_pages: int = 2
    db_path: str = "seen.sqlite"
    proxy: str = ""   # z. B. "socks5h://127.0.0.1:9050" für Tor; leer = kein Proxy
    use_browser: bool = False        # #1: Playwright-Backend für geblockte Portale
    verify_details: bool = False     # #4: Kleinanzeigen-Detailseiten anreichern
    suspicious_discount: float = 0.6  # #5: ab diesem Rabatt gilt ein Inserat als verdächtig
    home_zip: str = "68766"          # eigene PLZ – Basis für die Entfernungsanzeige
    max_parallel_searches: int = 2   # wie viele Suchen gleichzeitig laufen (CPU-Deckel)


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class HomeAssistantConfig:
    """Benachrichtigung über Home Assistant (nur im Add-on-Kontext aktiv)."""
    persistent: bool = False      # persistente Benachrichtigung in HA erzeugen
    notify_service: str = ""      # z. B. "notify.mobile_app_pixel" (optional)


@dataclass
class NotifyConfig:
    console: bool = True
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    home_assistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)


@dataclass
class Config:
    settings: Settings
    portals: dict
    searches: List[SearchQuery]
    notify: NotifyConfig

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Konfigurationsdatei nicht gefunden: {path}\n"
                "Kopiere crawler.example.yaml nach crawler.yaml und passe sie an."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        s = data.get("settings", {}) or {}
        settings = Settings(
            deal_threshold=float(s.get("deal_threshold", 0.15)),
            min_comparables=int(s.get("min_comparables", 5)),
            request_delay=float(s.get("request_delay", 2.5)),
            max_pages=int(s.get("max_pages", 2)),
            db_path=str(s.get("db_path", "seen.sqlite")),
            proxy=str(s.get("proxy", "") or ""),
            use_browser=bool(s.get("use_browser", False)),
            verify_details=bool(s.get("verify_details", False)),
            suspicious_discount=float(s.get("suspicious_discount", 0.6)),
            home_zip=str(s.get("home_zip", "68766") or "68766").strip(),
            max_parallel_searches=max(1, int(s.get("max_parallel_searches", 2))),
        )

        portals = data.get("portals", {}) or {}

        searches = [SearchQuery.from_dict(x) for x in (data.get("searches") or [])]
        if not searches:
            raise ValueError("Keine Suchen in der Konfiguration definiert.")

        n = data.get("notify", {}) or {}
        tg = (n.get("telegram", {}) or {})
        ha = (n.get("home_assistant", {}) or {})
        notify = NotifyConfig(
            console=bool(n.get("console", True)),
            telegram=TelegramConfig(
                enabled=bool(tg.get("enabled", False)),
                bot_token=str(tg.get("bot_token", "") or ""),
                chat_id=str(tg.get("chat_id", "") or ""),
            ),
            home_assistant=HomeAssistantConfig(
                persistent=bool(ha.get("persistent", False)),
                notify_service=str(ha.get("notify_service", "") or ""),
            ),
        )

        return cls(settings=settings, portals=portals, searches=searches, notify=notify)
