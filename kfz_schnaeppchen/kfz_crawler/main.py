"""CLI-Einstiegspunkt: Suchen ausführen, Schnäppchen finden, benachrichtigen."""

from __future__ import annotations

import argparse
import sys
from typing import List

from rich.console import Console

from .config import Config
from .dealfinder import dedupe, find_deals
from .models import Listing, SearchQuery, matches_query
from .notify import notify_all
from .portals import REGISTRY
from .portals.base import PortalError
from .storage import SeenStore

console = Console()


def run_search(cfg: Config, query: SearchQuery, store: SeenStore) -> List[Listing]:
    """Führt eine Suche auf allen aktiven Portalen aus und liefert neue Deals."""
    all_listings: List[Listing] = []

    for key, active in cfg.portals.items():
        if not active:
            continue
        portal_cls = REGISTRY.get(key)
        if portal_cls is None:
            console.print(f"[yellow]Unbekanntes Portal in config: {key}[/yellow]")
            continue

        portal = portal_cls(
            request_delay=cfg.settings.request_delay,
            max_pages=cfg.settings.max_pages,
            proxy=cfg.settings.proxy or None,
            render=cfg.settings.use_browser,
        )
        try:
            found = portal.search(query)
            # Homogenisierung: Felder, die die Trefferliste nicht liefert
            # (z. B. Kleinanzeigen-Kraftstoff/Getriebe/Leistung), per Detailseite
            # nachladen, damit der gemeinsame Filtersatz überall greift.
            # verify_details erzwingt zusätzlich die Anreicherung auch ohne solche
            # Filter (force).
            if hasattr(portal, "enrich"):
                found = portal.enrich(found, query, force=cfg.settings.verify_details)
            console.print(f"  [dim]{portal.name}: {len(found)} Treffer[/dim]")
            all_listings.extend(found)
        except PortalError as e:
            console.print(f"  [yellow]{e}[/yellow]")
        except Exception as e:  # pragma: no cover - robuster Lauf trotz Portalfehler
            console.print(f"  [red]{portal.name}: Fehler – {e}[/red]")

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

    # ALLE (neuen, nicht-dublettigen) Inserate für die UI speichern – Schnäppchen
    # sind über is_deal markiert. Zurückgegeben werden nur neue Deals (für Meldung).
    new_deals = []
    for l in result.priced:
        if not store.is_new(l):
            continue
        # Cross-Lauf-Dublette (gleiches Auto in früherem Lauf/anderem Portal)?
        if hasattr(store, "similar_exists") and store.similar_exists(l.year, l.mileage, l.price):
            store.mark_seen(l)  # merken, aber nicht doppelt anzeigen
            continue
        store.mark_seen(l)
        if hasattr(store, "record_listing"):
            store.record_listing(query.name, l)
        if l.is_deal:
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
