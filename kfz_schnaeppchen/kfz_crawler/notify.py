"""Benachrichtigungen: Konsole und optional Telegram."""

from __future__ import annotations

import os
from typing import List

import requests
from rich.console import Console
from rich.table import Table

from .config import NotifyConfig
from .models import Listing

console = Console()


def notify_console(search_name: str, deals: List[Listing]) -> None:
    if not deals:
        return
    table = Table(title=f"🚗 Schnäppchen für: {search_name}", show_lines=False)
    table.add_column("Portal", style="cyan", no_wrap=True)
    table.add_column("Fahrzeug", style="white")
    table.add_column("Preis", justify="right", style="green")
    table.add_column("Markt", justify="right", style="dim")
    table.add_column("Rabatt", justify="right", style="bold magenta")
    table.add_column("Link", style="blue")

    for d in deals:
        price = f"{d.price:,} €".replace(",", ".") if d.price else "-"
        market = f"{d.market_price:,} €".replace(",", ".") if d.market_price else "-"
        disc = f"-{d.discount * 100:.0f} %" if d.discount is not None else "-"
        table.add_row(d.portal, str(d), price, market, disc, d.url)

    console.print(table)


def notify_telegram(cfg, search_name: str, deals: List[Listing]) -> None:
    if not deals or not cfg.enabled or not cfg.bot_token or not cfg.chat_id:
        return
    lines = [f"🚗 *Schnäppchen: {search_name}*", ""]
    for d in deals:
        price = f"{d.price:,} €".replace(",", ".") if d.price else "-"
        disc = f"-{d.discount * 100:.0f} %" if d.discount is not None else ""
        lines.append(f"{disc}  {d}\n{d.url}")
    text = "\n\n".join(lines)

    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage",
            data={
                "chat_id": cfg.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        console.print(f"[red]Telegram-Fehler: {e}[/red]")


def _deal_lines(deals: List[Listing]) -> str:
    lines = []
    for d in deals:
        disc = f"-{d.discount * 100:.0f}%" if d.discount is not None else ""
        lines.append(f"{disc}  {d}\n{d.url}")
    return "\n\n".join(lines)


def notify_home_assistant(cfg, search_name: str, deals: List[Listing]) -> None:
    """Meldet Deals an Home Assistant über die Supervisor-Proxy-API.

    Funktioniert nur, wenn der SUPERVISOR_TOKEN gesetzt ist (also innerhalb
    eines HA-Add-ons). Außerhalb wird still nichts getan.
    """
    if not deals:
        return
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    if not cfg.persistent and not cfg.notify_service:
        return

    base = "http://supervisor/core/api/services"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    title = f"🚗 KFZ Schnäppchen: {search_name} ({len(deals)})"
    message = _deal_lines(deals)

    try:
        if cfg.persistent:
            requests.post(
                f"{base}/persistent_notification/create",
                headers=headers,
                json={
                    "title": title,
                    "message": message,
                    "notification_id": f"kfz_schnaeppchen_{search_name}",
                },
                timeout=15,
            )
        if cfg.notify_service:
            svc = cfg.notify_service.replace("notify.", "").strip()
            if svc:
                requests.post(
                    f"{base}/notify/{svc}",
                    headers=headers,
                    json={"title": title, "message": message},
                    timeout=15,
                )
    except requests.RequestException as e:
        console.print(f"[red]Home-Assistant-Benachrichtigung fehlgeschlagen: {e}[/red]")


def notify_all(cfg: NotifyConfig, search_name: str, deals: List[Listing]) -> None:
    if cfg.console:
        notify_console(search_name, deals)
    if cfg.telegram.enabled:
        notify_telegram(cfg.telegram, search_name, deals)
    notify_home_assistant(cfg.home_assistant, search_name, deals)
