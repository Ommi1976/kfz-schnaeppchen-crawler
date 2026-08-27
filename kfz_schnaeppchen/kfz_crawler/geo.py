"""Offline Entfernungsberechnung für deutsche Postleitzahlen (Luftlinie via Haversine)."""

from __future__ import annotations

import math
import re
from typing import Optional, Tuple

# PLZ 2-Stellungs Koordinaten-Schwerpunkte für Deutschland (schnell, präzise & leichtgewichtig)
_PLZ2_COORDS = {
    "01": (51.05, 13.74), "02": (51.15, 14.99), "03": (51.76, 14.33), "04": (51.34, 12.38),
    "06": (51.48, 11.97), "07": (50.88, 11.59), "08": (50.50, 12.37), "09": (50.83, 12.92),
    "10": (52.52, 13.40), "12": (52.45, 13.43), "13": (52.57, 13.35), "14": (52.40, 13.06),
    "15": (52.34, 14.55), "16": (52.83, 13.83), "17": (53.56, 13.26), "18": (54.09, 12.13),
    "19": (53.63, 11.41), "20": (53.55, 9.99),  "21": (53.38, 10.23), "22": (53.60, 10.05),
    "23": (53.87, 10.69), "24": (54.32, 10.13), "25": (54.48, 9.05),  "26": (53.14, 8.22),
    "27": (53.55, 8.58),  "28": (53.08, 8.80),  "29": (52.62, 10.08), "30": (52.37, 9.74),
    "31": (52.15, 9.95),  "32": (52.02, 8.53),  "33": (51.72, 8.75),  "34": (51.32, 9.50),
    "35": (50.58, 8.68),  "36": (50.55, 9.68),  "37": (51.53, 9.93),  "38": (52.26, 10.53),
    "39": (52.13, 11.63), "40": (51.22, 6.78),  "41": (51.19, 6.44),  "42": (51.26, 7.15),
    "43": (51.58, 7.22),  "44": (51.51, 7.46),  "45": (51.46, 7.01),  "46": (51.52, 6.93),
    "47": (51.43, 6.76),  "48": (51.96, 7.63),  "49": (52.27, 8.05),  "50": (50.94, 6.96),
    "51": (50.98, 7.12),  "52": (50.78, 6.08),  "53": (50.73, 7.10),  "54": (49.76, 6.64),
    "55": (49.99, 8.27),  "56": (50.36, 7.60),  "57": (50.87, 8.02),  "58": (51.37, 7.47),
    "59": (51.68, 8.35),  "60": (50.11, 8.68),  "61": (50.23, 8.60),  "62": (50.08, 8.24),
    "63": (50.10, 8.77),  "64": (49.87, 8.65),  "65": (50.08, 8.24),  "66": (49.23, 7.00),
    "67": (49.48, 8.47),  "68": (49.49, 8.47),  "69": (49.41, 8.69),  "70": (48.78, 9.18),
    "71": (48.70, 9.00),  "72": (48.52, 9.06),  "73": (48.70, 9.65),  "74": (49.14, 9.22),
    "75": (48.89, 8.70),  "76": (49.01, 8.40),  "77": (48.47, 7.94),  "78": (48.06, 8.46),
    "79": (47.99, 7.85),  "80": (48.14, 11.58), "81": (48.11, 11.60), "82": (48.00, 11.35),
    "83": (47.85, 12.12), "84": (48.54, 12.15), "85": (48.76, 11.43), "86": (48.37, 10.90),
    "87": (47.73, 10.32), "88": (47.67, 9.48),  "89": (48.40, 9.99),  "90": (49.45, 11.08),
    "91": (49.59, 11.00), "92": (49.30, 12.12), "93": (49.02, 12.10), "94": (48.88, 12.96),
    "95": (50.04, 11.58), "96": (49.89, 10.89), "97": (49.79, 9.95),  "98": (50.60, 10.69),
    "99": (50.98, 11.03),
}


def parse_location(raw_location: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extrahiert (PLZ, Stadt) aus Texten wie 'DE-94447 Plattling', '80331 München'."""
    if not raw_location:
        return None, None
    text = str(raw_location).strip()
    # Muster: optional DE-, dann 5 Ziffern, dann Stadt
    m = re.search(r"\b(?:DE-)?(\d{5})\s+([A-Za-zÄÖÜäöüß\s\.\-]+)", text)
    if m:
        return m.group(1), m.group(2).strip()
    # Nur 5 Ziffern
    m_zip = re.search(r"\b(\d{5})\b", text)
    if m_zip:
        city = text.replace(m_zip.group(0), "").replace("DE-", "").strip()
        return m_zip.group(1), city or None
    return None, text or None


def calculate_distance_km(zip_from: Optional[str], zip_to: Optional[str]) -> Optional[int]:
    """Berechnet die ungefähre Entfernung (Luftlinie in km) zwischen zwei deutschen Postleitzahlen."""
    if not zip_from or not zip_to:
        return None
    
    z1 = re.sub(r"[^0-9]", "", str(zip_from))[:2]
    z2 = re.sub(r"[^0-9]", "", str(zip_to))[:2]
    
    if len(z1) < 2 or len(z2) < 2:
        return None
        
    c1 = _PLZ2_COORDS.get(z1)
    c2 = _PLZ2_COORDS.get(z2)
    
    if not c1 or not c2:
        return None
        
    if z1 == z2:
        return 15  # Gleicher 2-stelliger PLZ-Bezirk (~15 km)
        
    lat1, lon1 = math.radians(c1[0]), math.radians(c1[1])
    lat2, lon2 = math.radians(c2[0]), math.radians(c2[1])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist = 6371 * c  # Erdradius in km
    
    # Faktor ~1.2 für reale Straßenkilometer
    return max(10, int(round(dist * 1.18)))