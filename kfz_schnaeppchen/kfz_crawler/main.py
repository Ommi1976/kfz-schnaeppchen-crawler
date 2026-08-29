"""CLI-Einstiegspunkt: Suchen ausführen, Schnäppchen finden, benachrichtigen."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from rich.console import Console

from .config import Config
from .dealfinder import dedupe, find_deals
from .models import (
    Listing,
    SearchQuery,
    infer_listing_battery,
    infer_listing_details,
    infer_listing_range,
    matches_query,
)
from .notify import notify_all
from .portals import REGISTRY
from .portals.base import PortalError
from .storage import SeenStore

console = Console()


def _search_one_portal(cfg: Config, key: str, query: SearchQuery, store=None) -> List[Listing]:
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
        # WICHTIG: hier NUR Text-Auswertung (billig). Die teure Bild-OCR läuft
        # asynchron im Hintergrund-Daemon (run_background_image_enrichment) und
        # blockiert damit weder den Suchlauf noch die CPU während der Suche.
        matching = []
        home_zip = getattr(cfg.settings, "home_zip", "") or None
        for listing in found:
            infer_listing_battery(listing, check_images=False)
            infer_listing_range(listing)
            if home_zip:
                try:
                    infer_listing_details(listing, home_zip)
                except Exception:
                    pass
            if listing.fuel == "elektro" or listing.battery_kwh is not None:
                try:
                    from .ev_database import lookup_ev_spec
                    spec = lookup_ev_spec(listing.title, getattr(listing, "body", ""), power_ps=listing.power_ps)
                    if not spec and (listing.battery_kwh or listing.ev_range_km):
                        if store and hasattr(store, "record_discovered_ev_model"):
                            is_new, rec = store.record_discovered_ev_model(
                                listing.title,
                                battery_kwh=listing.battery_kwh,
                                ev_range_km=listing.ev_range_km,
                                power_ps=listing.power_ps,
                            )
                            if is_new:
                                logger.info("💡 Neues E-Modell entdeckt: %s (~%.1f kWh, ~%d km)", listing.title, listing.battery_kwh or 0, listing.ev_range_km or 0)
                except Exception:
                    pass

            if matches_query(listing, query):
                matching.append(listing)
                if store and hasattr(store, "record_listing"):
                    try:
                        store.record_listing(query.name, listing)
                    except Exception:
                        pass
        console.print(f"  [dim]{portal.name}: {len(matching)}/{len(found)} Treffer (passend/Roh)[/dim]")
        return matching
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
        futures = {ex.submit(_search_one_portal, cfg, k, query, store): k for k in active}
        for fut in as_completed(futures):
            all_listings.extend(fut.result())

    # Entfernung zur eigenen PLZ (Home) für alle Treffer berechnen.
    home_zip = getattr(cfg.settings, "home_zip", "") or None
    if home_zip:
        for l in all_listings:
            try:
                infer_listing_details(l, home_zip)
            except Exception:
                pass

    # Zentraler Nachfilter: erweiterte Kriterien (Getriebe, Leistung, Karosserie,
    # E-Auto-Reichweite …) portalübergreifend anwenden. Die Aufteilung wird
    # protokolliert, damit ein Portal mit Roh-Treffern nicht stillschweigend
    # aus der Anzeige verschwindet.
    before_filter = Counter(l.portal for l in all_listings)
    # #2 Portal-Health: liefert ein aktives Portal 0 Roh-Treffer, obwohl insgesamt
    # genug zusammenkommt, deutet das auf einen Parser-Bruch oder Block hin.
    total_raw = sum(before_filter.values())
    if total_raw >= cfg.settings.min_comparables:
        for key in active:
            pname = REGISTRY[key].name
            if before_filter.get(pname, 0) == 0:
                console.print(
                    f"  [yellow]⚠ Portal-Health: {pname} lieferte 0 Treffer – "
                    f"Parser/Block prüfen.[/yellow]"
                )
    all_listings = [l for l in all_listings if matches_query(l, query)]
    after_filter = Counter(l.portal for l in all_listings)
    if before_filter:
        portal_counts = ", ".join(
            f"{portal}: {after_filter.get(portal, 0)}/{count}"
            for portal, count in sorted(before_filter.items())
        )
        console.print(f"  [dim]Portalfilter: {portal_counts} (passend/Roh)[/dim]")

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
    portal_active_fps: Dict[str, Set[str]] = {}
    for p_name, count in before_filter.items():
        if count > 0:
            portal_active_fps[p_name] = set()

    for l in result.priced:
        if l.portal in portal_active_fps:
            portal_active_fps[l.portal].add(l.fingerprint)
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

    if hasattr(store, "sync_active_deals"):
        store.sync_active_deals(query.name, portal_active_fps)
    if hasattr(store, "purge_unmatching_deals"):
        store.purge_unmatching_deals(query.name, query)
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
