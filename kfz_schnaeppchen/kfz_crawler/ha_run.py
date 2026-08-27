"""Einstiegspunkt für den Betrieb als Home Assistant Add-on.

Liest die Add-on-Optionen aus /data/options.json (von Home Assistant
geschrieben), baut daraus die Konfiguration und führt die Suchen in einer
Endlosschleife im konfigurierten Intervall aus. Der Duplikat-Speicher liegt
in /data (persistent über Neustarts hinweg).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List

from rich.console import Console

from .config import (
    Config,
    HomeAssistantConfig,
    NotifyConfig,
    Settings,
    TelegramConfig,
)
from .main import run_search
from .models import SearchQuery
from .notify import notify_all
from .storage import SeenStore

console = Console()

OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def load_options() -> dict:
    path = Path(OPTIONS_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def build_config(opts: dict) -> Config:
    # deal_threshold kommt als Prozentwert (z. B. 15) aus der UI -> in Anteil wandeln.
    threshold_pct = float(opts.get("deal_threshold", 15))
    db_path = os.environ.get("KFZ_DB_PATH") or str(Path(DATA_DIR) / "seen.sqlite")
    settings = Settings(
        deal_threshold=threshold_pct / 100.0,
        min_comparables=int(opts.get("min_comparables", 5)),
        request_delay=float(opts.get("request_delay", 2.5)),
        max_pages=int(opts.get("max_pages", 2)),
        db_path=db_path,
        proxy=str(opts.get("proxy", "") or ""),
        use_browser=bool(opts.get("use_browser", False)),
        verify_details=bool(opts.get("verify_details", False)),
        # Prozentwert aus der UI (z. B. 60) -> Anteil.
        suspicious_discount=float(opts.get("suspicious_discount", 60)) / 100.0,
    )

    # Portale: Liste aktiver Portal-Keys -> dict {key: bool}
    active = set(opts.get("portals", []) or [])
    portals = {
        "autoscout24": "autoscout24" in active,
        "kleinanzeigen": "kleinanzeigen" in active,
        "autouncle": "autouncle" in active,
        "mobile_de": "mobile_de" in active,
        "heycar": "heycar" in active,
    }

    searches: List[SearchQuery] = [
        SearchQuery.from_dict(s) for s in (opts.get("searches") or [])
    ]

    notify = NotifyConfig(
        console=True,
        telegram=TelegramConfig(
            enabled=bool(opts.get("telegram_enabled", False)),
            bot_token=str(opts.get("telegram_bot_token", "") or ""),
            chat_id=str(opts.get("telegram_chat_id", "") or ""),
        ),
        home_assistant=HomeAssistantConfig(
            persistent=bool(opts.get("notify_persistent", True)),
            notify_service=str(opts.get("notify_service", "") or ""),
        ),
    )

    return Config(settings=settings, portals=portals, searches=searches, notify=notify)


def run_once(cfg: Config) -> int:
    store = SeenStore(cfg.settings.db_path)
    total = 0
    try:
        for query in cfg.searches:
            console.print(f"\n[bold]🔎 Suche: {query.name}[/bold]")
            deals = run_search(cfg, query, store)
            if deals:
                total += len(deals)
                notify_all(cfg.notify, query.name, deals)
            else:
                console.print("  [dim]Keine neuen Schnäppchen.[/dim]")
    finally:
        store.close()
    return total


def main() -> int:
    opts = load_options()
    cfg = build_config(opts)
    interval_min = int(opts.get("interval_minutes", 30))

    console.print(
        f"[bold green]KFZ Schnäppchen Add-on gestartet.[/bold green] "
        f"Intervall: {interval_min} min, "
        f"Suchen: {len(cfg.searches)}, "
        f"Portale aktiv: {[k for k, v in cfg.portals.items() if v]}"
    )

    if not cfg.searches:
        console.print("[red]Keine Suchen konfiguriert – bitte im Add-on einrichten.[/red]")
        return 1

    while True:
        started = time.time()
        try:
            total = run_once(cfg)
            console.print(f"[green]Durchlauf fertig: {total} neue Schnäppchen.[/green]")
        except Exception as e:  # pragma: no cover - Add-on soll nicht crashen
            console.print(f"[red]Fehler im Durchlauf: {e}[/red]")

        elapsed = time.time() - started
        sleep_s = max(60, interval_min * 60 - int(elapsed))
        console.print(f"[dim]Nächster Durchlauf in {sleep_s // 60} min…[/dim]")
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
