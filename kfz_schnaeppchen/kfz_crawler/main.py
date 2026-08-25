"""CLI-Einstiegspunkt: Suchen ausführen, Schnäppchen finden, benachrichtigen."""

from __future__ import annotations

import argparse
import sys
from typing import List

from rich.console import Console

from .config import Config
from .dealfinder import find_deals
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
            # #4: Kleinanzeigen-Treffer optional per Detailseite anreichern.
            if cfg.settings.verify_details and hasattr(portal, "enrich"):
                found = portal.enrich(found, query)
            console.print(f"  [dim]{portal.name}: {len(found)} Treffer[/dim]")
            all_listings.extend(found)
        except PortalError as e:
            console.print(f"  [yellow]{e}[/yellow]")
        except Exception as e:  # pragma: no cover - robuster Lauf trotz Portalfehler
            console.print(f"  [red]{portal.name}: Fehler – {e}[/red]")

    # Zentraler Nachfilter: erweiterte Kriterien (Getriebe, Leistung, Karosserie,
    # E-Auto-Reichweite …) portalübergreifend anwenden.
    all_listings = [l for l in all_listings if matches_query(l, query)]

    # #3/#5: erwarteten Preis (Regression) schätzen, Deals + Verdachtsfälle trennen.
    result = find_deals(
        all_listings,
        deal_threshold=cfg.settings.deal_threshold,
        min_comparables=cfg.settings.min_comparables,
        suspicious_discount=cfg.settings.suspicious_discount,
    )
    if result.market_median is not None:
        modus = "Regression" if result.used_regression else "Median"
        extra = f", {len(result.suspicious)} verdächtig unterdrückt" if result.suspicious else ""
        console.print(
            f"  [dim]Preismodell: {modus} (Median ~{result.market_median:,} €{extra})[/dim]".replace(",", ".")
        )

    # Nur neue (noch nicht gemeldete) Deals durchlassen.
    new_deals = [d for d in result.deals if store.is_new(d)]
    for d in new_deals:
        store.mark_seen(d)
        # Für die Weboberfläche dauerhaft festhalten.
        if hasattr(store, "record_deal"):
            store.record_deal(query.name, d)

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
