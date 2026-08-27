"""Batterie-Zustandsanalyse: Volltext-Detailanalyse und OCR von Zertifikatsbildern."""

from __future__ import annotations

import io
import logging
import re
from typing import List, Optional
import requests
from bs4 import BeautifulSoup

from kfz_crawler.models import Listing, extract_battery_soh, extract_ev_range_km, extract_battery_kwh

logger = logging.getLogger(__name__)

# Optionales Tesseract OCR & Pillow
try:
    from PIL import Image, ImageEnhance
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


def ocr_image_bytes(image_bytes: bytes) -> Optional[str]:
    """Führt eine schnelle optische Texterkennung auf Bilddaten durch."""
    if not HAS_OCR or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "L":
            img = img.convert("L")
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.8)
        
        if max(img.size) > 1600:
            img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            
        text = pytesseract.image_to_string(img, lang="deu+eng", config="--psm 6")
        return text
    except Exception as e:
        logger.debug("OCR-Fehler bei Bildanalyse: %s", e)
        return None


def extract_soh_from_image_urls(image_urls: List[str], max_images: int = 4, timeout: float = 4.0) -> Optional[float]:
    """Lädt bis zu max_images Inseratsbilder und sucht per OCR nach SoH / Prüfberichten."""
    if not HAS_OCR or not image_urls:
        return None

    sorted_urls = sorted(
        image_urls,
        key=lambda u: (
            0 if any(k in u.lower() for k in ["cert", "test", "dok", "doc", "bericht", "aviloo", "dekra", "tacho", "batterie"]) else 1
        )
    )

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })

    for url in sorted_urls[:max_images]:
        try:
            resp = sess.get(url, timeout=timeout)
            if resp.status_code != 200 or not resp.content:
                continue
            ocr_text = ocr_image_bytes(resp.content)
            if not ocr_text:
                continue
            soh = extract_battery_soh(ocr_text)
            if soh is not None:
                logger.info("SoH=%.1f%% per Bild-OCR gefunden in %s", soh, url)
                return soh
        except Exception as e:
            logger.debug("Fehler beim Herunterladen/Analysieren von %s: %s", url, e)
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
    """Prüft Volltext und optional Inseratsbilder auf den realen Akku-Zustand (SoH)."""
    if listing.battery_soh is not None:
        return True

    text = f"{listing.title or ''} {getattr(listing, 'body', '') or ''}"
    soh = extract_battery_soh(text)
    if soh is not None:
        listing.battery_soh = soh
        return True

    if image_urls:
        soh = extract_soh_from_image_urls(image_urls)
        if soh is not None:
            listing.battery_soh = soh
            return True

    return False