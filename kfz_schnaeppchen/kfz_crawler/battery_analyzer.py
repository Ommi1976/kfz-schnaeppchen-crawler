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
_TESS_CONFIG = "--psm 6 -c tessedit_char_whitelist=0123456789%.,ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:-()/äöüÄÖÜß "


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
    """Blitzschneller Vorfilter (< 1.5 ms), um normale Autobilder (Felgen, Lack, Sitze) sofort zu überspringen."""
    # 1. URL-Hinweise prüfen
    u_low = (url or "").lower()
    if any(k in u_low for k in ["cert", "test", "dok", "doc", "bericht", "aviloo", "dekra", "tuev", "tüv", "tacho", "batterie", "soh", "diag", "screen", "check"]):
        return True

    try:
        # 2. Extrem kleiner Thumbnail für < 1ms Heuristik
        thumb = img.resize((32, 32), Image.Resampling.NEAREST).convert("L")
        stat = ImageStat.Stat(thumb)
        mean_val = stat.mean[0]
        stddev_val = stat.stddev[0]

        # Sehr helle Bilder / weiße Dokumente (AVILOO, DEKRA, TÜV)
        if mean_val > 175:
            return True
        # Helles Dokument mit Text & Tabellen
        if mean_val > 140 and stddev_val > 15:
            return True
        # Diagnose-Screenshots / Bordcomputer: Dunkler Screen mit hellem Text
        if mean_val < 90 and stddev_val > 20:
            return True
        # Hohe Varianz (Text-Tabellen)
        if stddev_val > 55:
            return True

        return False
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
        gray = img.convert("L")
        gray = ImageOps.autocontrast(gray, cutoff=1)
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(1.8)

        # Schnelle Tesseract-Erkennung mit Whitelist
        text = pytesseract.image_to_string(enhanced, lang="deu+eng", config=_TESS_CONFIG)
        return text
    except Exception as e:
        logger.debug("OCR-Fehler bei Bildanalyse: %s", e)
        return None


def _fetch_and_ocr_single_image(url: str, sess: requests.Session, timeout: float = 4.0) -> Tuple[str, Optional[float]]:
    """Lädt ein einzelnes Bild herunter und führt blitzschnelle OCR durch."""
    try:
        hd_url = upgrade_image_url_to_highres(url)
        resp = sess.get(hd_url, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return url, None

        text = ocr_image_bytes(resp.content, url=url)
        if not text:
            return url, None

        soh = extract_battery_soh(text)
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

    with ThreadPoolExecutor(max_workers=6) as executor:
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


def run_background_image_enrichment(store) -> int:
    """Scannt im Hintergrund alle bestehenden E-Auto-Inserate ohne SoH."""
    if not HAS_OCR:
        return 0

    rows = store.conn.execute(
        "SELECT fingerprint, title, image_urls FROM deals WHERE (fuel LIKE '%elektro%' OR fuel LIKE '%electric%') AND battery_soh IS NULL"
    ).fetchall()

    found = 0
    for r in rows:
        fp = r["fingerprint"]
        title = r["title"]
        imgs_json = r["image_urls"]
        if not imgs_json:
            continue
        try:
            urls = json.loads(imgs_json) if isinstance(imgs_json, str) else imgs_json
            if not urls:
                continue
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
    infer_listing_battery(listing, check_images=True)
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