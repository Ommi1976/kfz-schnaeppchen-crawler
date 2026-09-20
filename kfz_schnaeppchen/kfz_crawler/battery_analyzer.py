"""Batterie-Zustandsanalyse: Turbo-OCR-Pipeline mit intelligentem 2ms-Vorfilter und asynchronem Daemon."""

from __future__ import annotations

import io
import hashlib
import json
import logging
import re
import threading
import time
from statistics import median
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple
import requests
from bs4 import BeautifulSoup

from kfz_crawler.models import Listing, extract_battery_soh, extract_ev_range_km, extract_battery_kwh

logger = logging.getLogger(__name__)

# Optionales Tesseract OCR & Pillow
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
    import pytesseract
    # Auch das Tesseract-Binary prüfen: pytesseract allein reicht nicht, das
    # ausführbare tesseract muss vorhanden sein. Fehlt es, OCR komplett aus
    # (keine vergeblichen Bild-Downloads/OCR-Versuche pro Inserat).
    try:
        pytesseract.get_tesseract_version()
        HAS_OCR = True
    except Exception:
        HAS_OCR = False
except ImportError:
    HAS_OCR = False

# Tesseract Whitelist für maximale Geschwindigkeit (bis zu 5x schneller)
_TESS_WHITELIST = "0123456789%.,ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:-()/äöüÄÖÜß "


def _tess_cfg(psm: int) -> str:
    return f"--psm {psm} -c tessedit_char_whitelist={_TESS_WHITELIST}"


_OCR_LANG: Optional[str] = None


def _resolve_ocr_lang() -> str:
    """Ermittelt einmalig die verfügbare OCR-Sprache (deu+eng > eng > default)."""
    global _OCR_LANG
    if _OCR_LANG is not None:
        return _OCR_LANG
    lang = "eng"
    try:
        available = set(pytesseract.get_languages(config=""))
        if "deu" in available and "eng" in available:
            lang = "deu+eng"
        elif "deu" in available:
            lang = "deu"
        elif "eng" in available:
            lang = "eng"
    except Exception:
        lang = "eng"
    _OCR_LANG = lang
    return lang


# Negativ/Positiv-Cache pro Bild-URL: verhindert wiederholtes Herunterladen und
# OCR-en desselben Bildes über mehrere Inserate und Hintergrund-Durchläufe.
_URL_SOH_CACHE: dict = {}
_URL_CACHE_LOCK = threading.Lock()
_URL_CACHE_MAX = 5000


def upgrade_image_url_to_highres(url: str) -> str:
    """Wandelt Thumbnails der Portale in maximale HD-Auflösung um."""
    if not url:
        return url
    u = url
    # mobile.de: $_27.jpg, $_2.jpg -> $_20.jpg (High-Res)
    if "mobile.de" in u or "ebayimg.com" in u or "classistatic" in u:
        u = re.sub(r"_\d+\.(jpe?g|webp|png)", r"_20.\1", u, flags=re.I)
        u = re.sub(r"\$_\d+\.(jpe?g|webp|png)", r"$_57.\1", u, flags=re.I)
    # AutoScout24: /250x188.jpg -> /1280x960.jpg
    elif "autoscout24" in u or "as24" in u:
        u = re.sub(r"/\d+x\d+\.", r"/1280x960.", u)
    # Kleinanzeigen: $_2.JPG -> $_57.JPG
    elif "kleinanzeigen" in u:
        u = re.sub(r"\$_\d+\.jpe?g", r"$_57.JPG", u, flags=re.I)
    return u


def is_potential_document_or_screen(img: Image.Image, url: str = "") -> bool:
    """Vorfilter, der nur eindeutige Auto-Fotos verwirft und im Zweifel OCR zulässt.

    Zertifikate (AVILOO/DEKRA/TÜV) und Diagnose-Screens sind vielfältig: weiße
    Dokumente, farbige Gauge-Charts auf dunklem Grund, Bordcomputer-Anzeigen.
    Ein reines Auto-Foto (Lack, Felgen, Sitze) hat dagegen eine ausgewogene
    Mitten-Helligkeit UND geringe Kontrast-Streuung. Nur DIESE Kombination wird
    verworfen – alles andere geht in die OCR (der Early-Exit begrenzt die Kosten).
    """
    # 1. URL-Hinweise: klar dokumentartig -> immer prüfen
    u_low = (url or "").lower()
    if any(k in u_low for k in ["cert", "test", "dok", "doc", "bericht", "aviloo", "dekra", "tuev", "tüv", "tacho", "batterie", "soh", "diag", "screen", "check"]):
        return True

    try:
        thumb = img.resize((48, 48), Image.Resampling.NEAREST).convert("L")
        stat = ImageStat.Stat(thumb)
        mean_val = stat.mean[0]
        stddev_val = stat.stddev[0]

        # Reines Auto-Foto: mittlere Helligkeit (95..165) UND niedriger Kontrast
        # (stddev < 42). Nur solche Bilder werden übersprungen.
        if 95 <= mean_val <= 165 and stddev_val < 42:
            return False

        # Alles andere (helle Dokumente, dunkle Screens, kontrastreiche Charts)
        # kommt in die OCR.
        return True
    except Exception:
        return True


def ocr_image_bytes(image_bytes: bytes, url: str = "") -> Optional[str]:
    """Führt eine optimierte optische Texterkennung auf relevanten Bildern durch."""
    if not HAS_OCR or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Blitzschneller Vorfilter: Überspringe reine Autobilder
        if not is_potential_document_or_screen(img, url):
            return None

        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if w < 1000 and h < 1000:
            scale = 1300 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)
        elif max(w, h) > 2000:
            img.thumbnail((2000, 2000), Image.Resampling.BILINEAR)

        # Graustufen & Kontrast
        gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        enhanced = ImageEnhance.Contrast(gray).enhance(1.8)

        # Varianten: normal, invertiert + Zertifikats-Fokus-Crops (AVILOO/DEKRA)
        variants = [enhanced]
        try:
            if ImageStat.Stat(gray).mean[0] < 115:
                variants.append(ImageOps.invert(enhanced))
        except Exception:
            pass

        # Zertifikats-Crops: AVILOO Score-Kreis (oben rechts / Mitte)
        cw, ch = enhanced.size
        try:
            # Rechter oberer Quadrant (typisch für AVILOO Score)
            crop_tr = enhanced.crop((int(cw * 0.45), 0, cw, int(ch * 0.55)))
            variants.append(crop_tr)
            # Zentrierter Kasten (typisch für DEKRA / TÜV Ergebnis)
            crop_center = enhanced.crop((int(cw * 0.2), int(ch * 0.2), int(cw * 0.8), int(ch * 0.7)))
            variants.append(crop_center)
        except Exception:
            pass

        lang = _resolve_ocr_lang()
        collected: List[str] = []
        for var in variants:
            for psm in (6, 11):
                try:
                    t = pytesseract.image_to_string(var, lang=lang, config=_tess_cfg(psm))
                except Exception:
                    t = ""
                if t and t.strip():
                    collected.append(t)
                    # Early-Exit, sobald ein plausibler SoH gefunden ist.
                    if extract_battery_soh(t) is not None:
                        return "\n".join(collected)

        return "\n".join(collected) if collected else None
    except Exception as e:
        logger.debug("OCR-Fehler bei Bildanalyse: %s", e)
        return None


def _cache_get(url: str):
    with _URL_CACHE_LOCK:
        return _URL_SOH_CACHE.get(url, "miss")


def _cache_put(url: str, value: Optional[float]) -> None:
    with _URL_CACHE_LOCK:
        if len(_URL_SOH_CACHE) >= _URL_CACHE_MAX:
            _URL_SOH_CACHE.clear()
        _URL_SOH_CACHE[url] = value


def _fetch_and_ocr_single_image(url: str, sess: requests.Session, timeout: float = 4.0, fetch_bytes=None) -> Tuple[str, Optional[float]]:
    """Lädt ein einzelnes Bild herunter und führt blitzschnelle OCR durch."""
    # Cache: dasselbe Bild nicht erneut laden/OCR-en (auch None wird gemerkt).
    cached = _cache_get(url)
    if cached != "miss":
        return url, cached
    try:
        hd_url = upgrade_image_url_to_highres(url)
        if fetch_bytes:
            data = fetch_bytes(hd_url)
        else:
            resp = sess.get(hd_url, timeout=timeout)
            if resp.status_code != 200 or not resp.content:
                _cache_put(url, None)
                return url, None
            data = resp.content
        text = ocr_image_bytes(data, url=url)
        soh = extract_battery_soh(text) if text else None
        _cache_put(url, soh)
        return url, soh
    except Exception as e:
        if fetch_bytes:
            raise  # A deferred/failed download is NOT negative OCR evidence.
        logger.debug("Fehler beim OCR-Abruf von %s: %s", url, e)
        return url, None


def extract_soh_from_image_urls(image_urls: List[str], max_images: int = 15, timeout: float = 4.0, fetch_bytes=None) -> Optional[float]:
    """Prüft Bilder parallel und löst widersprüchliche OCR-Werte per Konsens."""
    if not HAS_OCR or not image_urls:
        return None

    sorted_urls = sorted(
        list(dict.fromkeys(image_urls)),
        key=lambda u: (
            0 if any(k in u.lower() for k in ["cert", "test", "dok", "doc", "bericht", "aviloo", "dekra", "tuev", "tüv", "tacho", "batterie", "soh", "diag"]) else 1
        )
    )[:max_images]

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })

    # Nur 3 Worker: die OCR ist CPU-lastig und läuft im Hintergrund – so bleibt
    # die Box benutzbar, während der Early-Exit die Latenz kurz hält.
    values: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=1 if fetch_bytes else 3) as executor:
        futures = {
            (executor.submit(_fetch_and_ocr_single_image, u, sess, timeout, fetch_bytes)
             if fetch_bytes else executor.submit(_fetch_and_ocr_single_image, u, sess, timeout)): u
            for u in sorted_urls}
        for fut in as_completed(futures):
            try:
                url, soh = fut.result()
                if soh is not None:
                    values.append((url, soh))
            except Exception:
                if fetch_bytes:
                    for pending in futures:
                        pending.cancel()
                    raise
                continue

    if not values:
        return None
    if len(values) == 1:
        url, soh = values[0]
        logger.info("SoH=%.1f%% per OCR gefunden in %s", soh, url)
        return soh
    numbers = [value for _, value in values]
    if max(numbers) - min(numbers) <= 2.5:
        result = round(float(median(numbers)), 1)
        logger.info("SoH=%.1f%% per OCR-Konsens aus %d Bildern", result, len(numbers))
        return result
    for candidate in numbers:
        cluster = [value for value in numbers if abs(value - candidate) <= 1.5]
        if len(cluster) >= 2:
            result = round(float(median(cluster)), 1)
            logger.info("SoH=%.1f%% per Mehrheitskonsens (%d/%d)", result, len(cluster), len(numbers))
            return result
    logger.warning("Widersprüchliche SoH-OCR-Werte verworfen: %s", numbers)
    return None


def _relevant_detail_text(soup: BeautifulSoup) -> str:
    """Begrenzt Felderkennung auf das eigentliche Inserat statt Empfehlungen."""
    # Remove related cars and page chrome before the broad fallback. A SoH
    # from a recommendation must never become this vehicle's battery health.
    for node in soup.select("script, style, nav, footer, [data-testid*='recommend'], [data-testid*='similar'], [data-testid*='listing-card']"):
        node.decompose()
    selectors = (
        "h1",
        "[data-testid*='battery']",
        "[data-testid*='description']",
        "[data-testid*='vehicle']",
        "[data-testid*='technical']",
    )
    parts: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            if text and text not in seen:
                seen.add(text)
                parts.append(text)
    if len(parts) <= 1:
        main = soup.select_one("main")
        return (main or soup).get_text(" ", strip=True)[:30000]
    return " ".join(parts)[:30000]


def mobile_detail_snapshot(html: str) -> str:
    """Cache only vehicle text and allowlisted gallery metadata, never scripts."""
    from html import escape
    from urllib.parse import urlparse
    soup = BeautifulSoup(html, "lxml")
    text = _relevant_detail_text(soup)  # Also removes recommendation galleries.
    images = []
    for img in soup.select("img[src], img[data-src]"):
        url = img.get("src") or img.get("data-src") or ""
        try:
            parsed = urlparse(url)
            allowed = (parsed.scheme == "https" and parsed.hostname in {"img.classistatic.de", "i.classistatic.de"}
                       and not parsed.username and not parsed.password and parsed.port in (None, 443))
        except ValueError:
            allowed = False
        if allowed:
            alt = img.get("alt", "")[:300]
            images.append(f'<img src="{escape(url, quote=True)}" alt="{escape(alt, quote=True)}">')
    return "<main>" + escape(text) + "".join(dict.fromkeys(images)) + "</main>"


def fetch_mobile_de_detail_data(raw_id: str, store=None) -> dict:
    """Compatibility helper using the same budgeted browser as the search."""
    if not raw_id or not str(raw_id).isdigit():
        return {}
    out = {}
    try:
        from .mobile_runtime import mobile_browser
        html = mobile_browser().fetch(
            f"https://suchen.mobile.de/fahrzeuge/details.html?id={raw_id}",
            store=store, kind="detail")
    except Exception:
        return {}

    soup = BeautifulSoup(html, "lxml")
    full_text = _relevant_detail_text(soup)

    # 1. SoH
    soh = extract_battery_soh(full_text)
    if soh is not None:
        out["battery_soh"] = soh

    # 2. Reichweite
    rng = extract_ev_range_km(full_text)
    if rng is not None:
        out["ev_range_km"] = rng

    # 3. Batterie-kWh
    kwh = extract_battery_kwh(full_text)
    if kwh is not None:
        out["battery_kwh"] = kwh

    # 4. Garantie
    from kfz_crawler.models import extract_warranty
    warr = extract_warranty(full_text)
    if warr:
        out["warranty"] = warr

    # 5. Bilder
    imgs = [img.get("src") or img.get("data-src") for img in soup.select("img[src], img[data-src]")]
    valid_imgs = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
    if valid_imgs:
        out["image_urls"] = valid_imgs

    return out


# Bereits erfolglos geprüfte Inserate (pro Prozess), damit hoffnungslose
# Inserate nicht bei jedem Hintergrund-Lauf erneut heruntergeladen/OCR-t werden.
_OCR_TRIED_FP: set = set()
# Obergrenze an Inseraten pro Hintergrund-Durchlauf – hält die CPU-Last gedeckelt.
_BG_MAX_LISTINGS_PER_PASS = 40


def run_background_image_enrichment(store, max_listings: int = _BG_MAX_LISTINGS_PER_PASS, proxy=None) -> int:
    """Bounded OCR; mobile details are handled in the normal enrichment queue."""
    with store._lock:
        rows = store.conn.execute(
        "SELECT fingerprint, portal, url, title, image_urls, evidence_json FROM deals "
        "WHERE (fuel LIKE '%elektro%' OR fuel LIKE '%electric%') "
        "AND battery_soh IS NULL AND COALESCE(is_stale, 0) = 0 ORDER BY last_seen DESC"
        ).fetchall()

    found = 0
    processed = 0
    updates: list[dict] = []
    for r in rows:
        fp = r["fingerprint"]
        if r["portal"] != "mobile.de" and fp in _OCR_TRIED_FP:
            continue
        if processed >= max_listings:
            break
        processed += 1  # every attempted listing counts, not just the OCR branch
        title = r["title"]
        portal = r["portal"] or ""
        url = r["url"] or ""
        imgs_json = r["image_urls"]

        # No independent requests/Chromium detail path bypassing the breaker.
        if not HAS_OCR or not imgs_json:
            continue
        try:
            urls = json.loads(imgs_json) if isinstance(imgs_json, str) else imgs_json
            if not urls:
                continue
            mobile = "mobile" in portal.lower() or "mobile.de" in url
            if mobile:
                from .mobile_runtime import load_state, save_state, mobile_browser, mobile_status
                if mobile_status(store).get("blocked_until", 0) > time.time():
                    continue
                evidence = json.loads(r["evidence_json"] or "{}")
                documents = evidence.get("certificate_images", {}).get("urls", [])
                cache_key = "mobile.ocr.v1." + hashlib.sha256(json.dumps([urls, documents]).encode()).hexdigest()
                cached = load_state(store, cache_key)
                if cached.get("until", 0) > time.time():
                    continue
                # Only document-like URLs; don't download entire vehicle galleries
                # speculatively. A negative result survives process restarts.
                urls = [u for u in urls if u in documents or re.search(r"cert|aviloo|dekra|bericht|batter|soh|diagnos", u, re.I)]
                if not urls:
                    save_state(store, cache_key, {"until": time.time() + 3 * 86400})
                    continue
            if len(_OCR_TRIED_FP) > 20000:
                _OCR_TRIED_FP.clear()
            if not mobile:
                _OCR_TRIED_FP.add(fp)
            if mobile:
                soh = extract_soh_from_image_urls(urls, max_images=3,
                    fetch_bytes=lambda u: mobile_browser().fetch_image(u, store=store, proxy=proxy))
                save_state(store, cache_key, {"until": time.time() + 3 * 86400})
            else:
                soh = extract_soh_from_image_urls(urls, max_images=10)
            if soh:
                updates.append({
                    "fingerprint": fp,
                    "battery_soh": soh,
                    "soh_source": "ocr_consensus",
                    "soh_confidence": 0.86,
                    "soh_evidence": "Bild-/Dokumentanalyse",
                })
                found += 1
                logger.info("Hintergrund-OCR: SoH=%.1f%% für %s gespeichert", soh, title[:50])
        except Exception as e:
            logger.debug("Hintergrund-OCR Fehler für %s: %s", title[:40], e)

    if updates:
        if hasattr(store, "update_enrichments"):
            store.update_enrichments(updates)
        else:
            for update in updates:
                store.conn.execute(
                    "UPDATE deals SET battery_soh=COALESCE(?, battery_soh) WHERE fingerprint=?",
                    (update.get("battery_soh"), update["fingerprint"]),
                )
            store.conn.commit()
    return found


def parse_mobile_de_detail_html(html: str, listing: Listing) -> None:
    """Extrahiert Batterie-Information, Detailtext und Galeriebilder aus der mobile.de Detailseite."""
    if not html:
        return
    soup = BeautifulSoup(html, "lxml")

    full_text = _relevant_detail_text(soup)
    if full_text and full_text not in (listing.body or ""):
        listing.body = f"{getattr(listing, 'body', '') or ''} {full_text}".strip()

    imgs = [img.get("src") or img.get("data-src") for img in soup.select("img[src], img[data-src]")]
    valid_imgs = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
    if valid_imgs:
        existing = getattr(listing, "image_urls", []) or []
        for img_url in valid_imgs:
            if img_url not in existing:
                existing.append(img_url)
        listing.image_urls = existing

    documents = []
    for img in soup.select("img[src], img[data-src]"):
        url = img.get("src") or img.get("data-src")
        document_alt = re.search(r"cert|zertifikat|aviloo|dekra|prüfbericht|batterietest", img.get("alt", ""), re.I)
        document_url = re.search(r"cert|aviloo|dekra|bericht|batter|soh|diagnos", url or "", re.I)
        if url in valid_imgs and (document_alt or document_url):
            documents.append(url)
    if documents:
        listing.field_evidence["certificate_images"] = {"source": "detail_gallery", "urls": list(dict.fromkeys(documents))}

    from kfz_crawler.models import infer_listing_details
    # Nur Text-Auswertung im Suchpfad – Bild-OCR erledigt der Hintergrund-Daemon.
    infer_listing_details(listing)


def enrich_listing_battery_deep(listing: Listing, image_urls: Optional[List[str]] = None) -> bool:
    """Prüft Volltext und Inseratsbilder auf den realen Akku-Zustand (SoH)."""
    if listing.battery_soh is not None:
        return True

    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    soh = extract_battery_soh(text)
    if soh is not None:
        listing.battery_soh = soh
        return True

    imgs = image_urls or getattr(listing, "image_urls", None)
    if imgs:
        soh = extract_soh_from_image_urls(imgs)
        if soh is not None:
            listing.battery_soh = soh
            return True

    return False
