"""Batterie-Zustandsanalyse: Turbo-OCR-Pipeline mit intelligentem 2ms-Vorfilter und asynchronem Daemon."""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
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

        # Varianten: normal + (bei dunklem Bild) invertiert. Dunkle Diagnose-
        # Screens haben hellen Text auf dunklem Grund – Tesseract braucht das
        # Gegenteil, deshalb zusätzlich invertiert erkennen.
        variants = [enhanced]
        try:
            if ImageStat.Stat(gray).mean[0] < 115:
                variants.append(ImageOps.invert(enhanced))
        except Exception:
            pass

        lang = _resolve_ocr_lang()
        collected: List[str] = []
        # Zwei PSM-Modi: 6 = Textblock (Tabellen/Zertifikate),
        # 11 = verstreute Zeichen (große Gauge-/Tacho-Zahlen).
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


def _fetch_and_ocr_single_image(url: str, sess: requests.Session, timeout: float = 4.0) -> Tuple[str, Optional[float]]:
    """Lädt ein einzelnes Bild herunter und führt blitzschnelle OCR durch."""
    # Cache: dasselbe Bild nicht erneut laden/OCR-en (auch None wird gemerkt).
    cached = _cache_get(url)
    if cached != "miss":
        return url, cached
    try:
        hd_url = upgrade_image_url_to_highres(url)
        resp = sess.get(hd_url, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            _cache_put(url, None)
            return url, None

        text = ocr_image_bytes(resp.content, url=url)
        soh = extract_battery_soh(text) if text else None
        _cache_put(url, soh)
        return url, soh
    except Exception as e:
        logger.debug("Fehler beim OCR-Abruf von %s: %s", url, e)
        return url, None


def extract_soh_from_image_urls(image_urls: List[str], max_images: int = 15, timeout: float = 4.0) -> Optional[float]:
    """Prüft Inseratsbilder parallel mit Vorfilter und Early-Exit."""
    if not HAS_OCR or not image_urls:
        return None

    sorted_urls = sorted(
        image_urls[:max_images],
        key=lambda u: (
            0 if any(k in u.lower() for k in ["cert", "test", "dok", "doc", "bericht", "aviloo", "dekra", "tuev", "tüv", "tacho", "batterie", "soh", "diag"]) else 1
        )
    )

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })

    # Nur 3 Worker: die OCR ist CPU-lastig und läuft im Hintergrund – so bleibt
    # die Box benutzbar, während der Early-Exit die Latenz kurz hält.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_and_ocr_single_image, u, sess, timeout): u for u in sorted_urls}
        for fut in as_completed(futures):
            try:
                url, soh = fut.result()
                if soh is not None:
                    logger.info("⚡ SoH=%.1f%% per Turbo-OCR gefunden in %s", soh, url)
                    return soh
            except Exception:
                continue

    return None


def fetch_mobile_de_detail_data(raw_id: str) -> dict:
    """Lädt die unblockierte mobile.de Detailseite und extrahiert SoH, Reichweite, Kapazität, Garantie und Bilder."""
    if not raw_id:
        return {}
    out = {}
    target_urls = [
        f"https://suchen.mobile.de/auto-inserat/car/{raw_id}.html",
        f"https://m.mobile.de/auto-inserat/car/{raw_id}.html",
    ]
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    })
    html = ""
    for url in target_urls:
        try:
            resp = sess.get(url, headers=sess.headers, timeout=5.0)
            if resp.status_code == 200 and len(resp.text) > 4000:
                html = resp.text
                break
        except Exception:
            continue

    if not html:
        try:
            from kfz_crawler.browser import fetch_rendered
            html = fetch_rendered(target_urls[0], engine="firefox")
        except Exception:
            pass

    if not html:
        return {}

    soup = BeautifulSoup(html, "lxml")
    full_text = soup.get_text(" ", strip=True)

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

    # 6. Bild-OCR Fallback
    if "battery_soh" not in out and valid_imgs:
        img_soh = extract_soh_from_image_urls(valid_imgs, max_images=8)
        if img_soh:
            out["battery_soh"] = img_soh

    return out


# Bereits erfolglos geprüfte Inserate (pro Prozess), damit hoffnungslose
# Inserate nicht bei jedem Hintergrund-Lauf erneut heruntergeladen/OCR-t werden.
_OCR_TRIED_FP: set = set()
# Obergrenze an Inseraten pro Hintergrund-Durchlauf – hält die CPU-Last gedeckelt.
_BG_MAX_LISTINGS_PER_PASS = 40


def run_background_image_enrichment(store, max_listings: int = _BG_MAX_LISTINGS_PER_PASS) -> int:
    """Scannt im Hintergrund E-Auto-Inserate ohne SoH (inkl. blockfreiem mobile.de Detailabruf & Turbo-OCR)."""
    rows = store.conn.execute(
        "SELECT fingerprint, portal, url, title, image_urls FROM deals WHERE (fuel LIKE '%elektro%' OR fuel LIKE '%electric%') AND battery_soh IS NULL"
    ).fetchall()

    found = 0
    processed = 0
    for r in rows:
        fp = r["fingerprint"]
        if fp in _OCR_TRIED_FP:
            continue
        if processed >= max_listings:
            break
        title = r["title"]
        portal = r["portal"] or ""
        url = r["url"] or ""
        imgs_json = r["image_urls"]

        # 1. mobile.de: Detaildaten blockfrei im Hintergrund abrufen
        if "mobile" in portal.lower() or "mobile.de" in url:
            m = re.search(r"id=(\d+)", url) or re.search(r"/(\d+)\.html", url)
            raw_id = m.group(1) if m else None
            if raw_id:
                try:
                    det = fetch_mobile_de_detail_data(raw_id)
                    if det:
                        soh = det.get("battery_soh")
                        rng = det.get("ev_range_km")
                        kwh = det.get("battery_kwh")
                        warr = det.get("warranty")
                        new_imgs = det.get("image_urls")
                        imgs_str = json.dumps(new_imgs, ensure_ascii=False) if new_imgs else None

                        store.conn.execute(
                            "UPDATE deals SET "
                            "battery_soh = COALESCE(?, battery_soh), "
                            "ev_range_km = COALESCE(?, ev_range_km), "
                            "battery_kwh = COALESCE(?, battery_kwh), "
                            "warranty = COALESCE(?, warranty), "
                            "image_urls = COALESCE(?, image_urls) "
                            "WHERE fingerprint = ?",
                            (soh, rng, kwh, warr, imgs_str, fp)
                        )
                        store.conn.commit()
                        if soh:
                            found += 1
                            logger.info("⚡ mobile.de Detail-Sync: SoH=%.1f%% für %s gespeichert", soh, title[:50])
                            _OCR_TRIED_FP.add(fp)
                            continue
                except Exception as e:
                    logger.debug("mobile.de Detail-Sync Fehler für %s: %s", title[:40], e)

        # 2. Bild-OCR Fallback für andere Portale & Galerien
        if not HAS_OCR or not imgs_json:
            continue
        try:
            urls = json.loads(imgs_json) if isinstance(imgs_json, str) else imgs_json
            if not urls:
                continue
            processed += 1
            if len(_OCR_TRIED_FP) > 20000:
                _OCR_TRIED_FP.clear()
            _OCR_TRIED_FP.add(fp)
            soh = extract_soh_from_image_urls(urls, max_images=10)
            if soh:
                store.conn.execute("UPDATE deals SET battery_soh = ? WHERE fingerprint = ?", (soh, fp))
                store.conn.commit()
                found += 1
                logger.info("Hintergrund-OCR: SoH=%.1f%% für %s gespeichert", soh, title[:50])
        except Exception as e:
            logger.debug("Hintergrund-OCR Fehler für %s: %s", title[:40], e)

    return found


def parse_mobile_de_detail_html(html: str, listing: Listing) -> None:
    """Extrahiert Batterie-Information, Detailtext und Galeriebilder aus der mobile.de Detailseite."""
    if not html:
        return
    soup = BeautifulSoup(html, "lxml")

    full_text = soup.get_text(" ", strip=True)
    if full_text:
        listing.body = f"{getattr(listing, 'body', '') or ''} {full_text}".strip()

    imgs = [img.get("src") or img.get("data-src") for img in soup.select("img[src], img[data-src]")]
    valid_imgs = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
    if valid_imgs:
        existing = getattr(listing, "image_urls", []) or []
        for img_url in valid_imgs:
            if img_url not in existing:
                existing.append(img_url)
        listing.image_urls = existing

    from kfz_crawler.models import infer_listing_battery, infer_listing_range
    # Nur Text-Auswertung im Suchpfad – Bild-OCR erledigt der Hintergrund-Daemon.
    infer_listing_battery(listing, check_images=False)
    infer_listing_range(listing)


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