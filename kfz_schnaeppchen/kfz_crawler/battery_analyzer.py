"""Batterie-Zustandsanalyse: Parallele High-Res OCR-Pipeline für Batteriezertifikate und Prüfberichte."""

from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple
import requests
from bs4 import BeautifulSoup

from kfz_crawler.models import Listing, extract_battery_soh, extract_ev_range_km, extract_battery_kwh

logger = logging.getLogger(__name__)

# Optionales Tesseract OCR & Pillow
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


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


def ocr_image_bytes(image_bytes: bytes) -> Optional[str]:
    """Führt eine präzise optische Texterkennung speziell für Zertifikate und Tabellen durch."""
    if not HAS_OCR or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")

        # 1. Bildgröße optimal skalieren (Zertifikate brauchen Schärfe)
        w, h = img.size
        if w < 1200 and h < 1200:
            scale = 1400 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        elif max(w, h) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.LANCZOS)

        # 2. Graustufen & Autokontrast
        gray = img.convert("L")
        gray = ImageOps.autocontrast(gray, cutoff=1)

        # 3. Kontrast anheben & Schärfen
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(2.0)
        sharp = enhanced.filter(ImageFilter.SHARPEN)

        # 4. Tesseract OCR mit deutscher & englischer Spracherkennung
        text = pytesseract.image_to_string(sharp, lang="deu+eng", config="--psm 6")
        if not text or len(text.strip()) < 10:
            text = pytesseract.image_to_string(sharp, lang="deu+eng", config="--psm 3")

        return text
    except Exception as e:
        logger.debug("OCR-Fehler bei Bildanalyse: %s", e)
        return None


def _fetch_and_ocr_single_image(url: str, sess: requests.Session, timeout: float = 5.0) -> Tuple[str, Optional[float]]:
    """Lädt ein einzelnes Bild in High-Res herunter und führt OCR durch."""
    try:
        hd_url = upgrade_image_url_to_highres(url)
        resp = sess.get(hd_url, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return url, None

        text = ocr_image_bytes(resp.content)
        if not text:
            return url, None

        soh = extract_battery_soh(text)
        return url, soh
    except Exception as e:
        logger.debug("Fehler beim OCR-Abruf von %s: %s", url, e)
        return url, None


def extract_soh_from_image_urls(image_urls: List[str], max_images: int = 12, timeout: float = 5.0) -> Optional[float]:
    """Prüft Inseratsbilder parallel mit Tesseract OCR auf SoH-Prüfberichte."""
    if not HAS_OCR or not image_urls:
        return None

    # Zertifikats-Verdächtige Bilder priorisieren
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

    # Paralleler Download & OCR
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_and_ocr_single_image, u, sess, timeout): u for u in sorted_urls}
        for fut in as_completed(futures):
            try:
                url, soh = fut.result()
                if soh is not None:
                    logger.info("SoH=%.1f%% per paralleler Bild-OCR gefunden in %s", soh, url)
                    return soh
            except Exception:
                continue

    return None


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