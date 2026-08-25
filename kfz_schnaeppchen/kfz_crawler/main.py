"""CLI-Einstiegspunkt: Suchen ausführen, Schnäppchen finden, benachrichtigen."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from rich.console import Console

from .config import Config
from .dealfinder import dedupe, find_deals
from .models import (
    Listing,
    SearchQuery,
    infer_listing_battery,
    infer_listing_range,
    matches_query,
)
from .notify import notify_all
from .portals import REGISTRY
from .portals.base import PortalError
from .storage import SeenStore

console = Console()


def _search_one_portal(cfg: Config, key: str, query: SearchQuery) -> List[Listing]:
    """Ein Portal abfragen (+ ggf. anreichern). Läuft in eigenem Thread."""
    portal_cls = REGISTRY.get(key)
    if portal_cls is None:
        console.print(f"[yellow]Unbekanntes Portal in config: {key}[/yellow]")
        return []
    portal = portal_cls(
        request_delay=cfg.settings.request_delay,
        max_pages=cfg.settings.max_pages,
        proxy=cfg.settings.proxy or None,
        render=cfg.settings.use_browser,
    )
    try:
        found = portal.search(query)
        # Homogenisierung: Felder, die die Trefferliste nicht liefert, per
        # Detailseite nachladen (verify_details erzwingt zusätzlich via force).
        if hasattr(portal, "enrich"):
            found = portal.enrich(found, query, force=cfg.settings.verify_details)
        # Viele Portale liefern die Akku-Kapazität nur im Titel (z. B. "62 kWh").
        for listing in found:
            infer_listing_battery(listing)
            infer_listing_range(listing)
        console.print(f"  [dim]{portal.name}: {len(found)} Treffer[/dim]")
        return found
    except PortalError as e:
        console.print(f"  [yellow]{e}[/yellow]")
    except Exception as e:  # pragma: no cover - robuster Lauf trotz Portalfehler
        console.print(f"  [red]{portal.name}: Fehler – {e}[/red]")
    return []


def run_search(cfg: Config, query: SearchQuery, store: SeenStore) -> List[Listing]:
    """Führt eine Suche auf allen aktiven Portalen aus und liefert neue Deals.

    Die Portale laufen PARALLEL (verschiedene Hosts) – das halbiert die Laufzeit,
    ohne einen einzelnen Host stärker zu belasten. Jedes Portal behält seine
    eigene höfliche Anfrage-Drosselung.
    """
    active = [k for k, on in cfg.portals.items() if on and REGISTRY.get(k)]
    all_listings: List[Listing] = []
    if not active:
        return []

    with ThreadPoolExecutor(max_workers=len(active)) as ex:
        futures = {ex.submit(_search_one_portal, cfg, k, query): k for k in active}
        for fut in as_completed(futures):
            all_listings.extend(fut.result())

    # Zentraler Nachfilter: erweiterte Kriterien (Getriebe, Leistung, Karosserie,
    # E-Auto-Reichweite …) portalübergreifend anwenden.
    all_listings = [l for l in all_listings if matches_query(l, query)]

    # Dublettenfilter: dasselbe Auto (Baujahr + exakter km) nur einmal.
    all_listings = dedupe(all_listings)

    # Preis-Modell + Klassifikation (Deal / verdächtig / Lockangebot) für ALLE.
    result = find_deals(
        all_listings,
        deal_threshold=cfg.settings.deal_threshold,
        min_comparables=cfg.settings.min_comparables,
        suspicious_discount=cfg.settings.suspicious_discount,
    )
    if result.market_median is not None:
        modus = "Regression" if result.used_regression else "Median"
        console.print(
            f"  [dim]Preismodell: {modus} (Median ~{result.market_median:,} €) – "
            f"{len(result.deals)} Deals, {len(result.priced)} Inserate[/dim]".replace(",", ".")
        )

    # ALLE aktuellen Inserate für die UI speichern – Schnäppchen sind über
    # is_deal markiert. Der seen-Store steuert nur Benachrichtigungen, nicht die
    # Anzeige: bekannte Fahrzeuge werden bei jedem Lauf aktualisiert.
    new_deals = []
    for l in result.priced:
        is_new = store.is_new(l)
        # Cross-Lauf-Dublette nur für neue Inserate unterdrücken; bekannte
        # Inserate werden trotzdem für die aktuelle UI-Anzeige aktualisiert.
        if is_new and hasattr(store, "similar_exists") and store.similar_exists(
            l.year, l.mileage, l.price
        ):
            store.mark_seen(l)  # merken, aber nicht doppelt anzeigen
            continue
        if is_new:
            store.mark_seen(l)
        if hasattr(store, "record_listing"):
            store.record_listing(query.name, l)
        if is_new and l.is_deal:
            new_deals.append(l)
    if hasattr(store, "prune"):
        store.prune()

    return new_deals


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KFZ Schnäppchen Crawler – findet Auto-Schnäppchen über mehrere Portale."
    )
    parser.add_argument(
        "-c", "--config", default="crawler.yaml", help="Pfad zur Konfigurationsdatei"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Auch bereits gesehene Inserate anzeigen (Duplikat-Filter ignorieren)",
    )
    args = parser.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        return 1

    store = SeenStore(cfg.settings.db_path)
    total_deals = 0

    try:
        for query in cfg.searches:
            console.print(f"\n[bold]🔎 Suche: {query.name}[/bold]")
            if args.all:
                # Frischer Store-Blick: temporär alles als neu behandeln.
                store_new = SeenStore(":memory:")
                deals = run_search(cfg, query, store_new)
                store_new.close()
            else:
                deals = run_search(cfg, query, store)

            if deals:
                total_deals += len(deals)
                notify_all(cfg.notify, query.name, deals)
            else:
                console.print("  [dim]Keine neuen Schnäppchen.[/dim]")
    finally:
        store.close()

    console.print(f"\n[bold green]Fertig. {total_deals} neue Schnäppchen gefunden.[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
