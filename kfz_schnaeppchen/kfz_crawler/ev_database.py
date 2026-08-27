"""Interne Referenzdatenbank für Elektrofahrzeuge (Akkukapazität Brutto/Netto & WLTP-Reichweite)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class EVSpec:
    make: str
    model: str
    variant: str
    battery_gross_kwh: float
    battery_net_kwh: float
    wltp_range_km: int
    power_kw: Optional[int] = None
    power_ps: Optional[int] = None
    patterns: List[re.Pattern] = None


# Umfassender Katalog aller gängigen E-Autos im deutschen Markt (Spezifische Trims VOR Fallbacks)
_EV_DATABASE: List[EVSpec] = [
    # --- VOLKSWAGEN ---
    EVSpec("Volkswagen", "ID.3", "Pro S", 82.0, 77.0, 553, 150, 204, [
        re.compile(r"\bid\.?3\b.*?\b(?:pro\s*s|pro-s)\b", re.I),
        re.compile(r"\bid\.?3\b.*?77\s*kwh", re.I),
    ]),
    EVSpec("Volkswagen", "ID.3", "Pure Performance / Pure", 55.0, 45.0, 352, 110, 150, [
        re.compile(r"\bid\.?3\b.*?\b(?:pure\s+performance|pure)\b", re.I),
        re.compile(r"\bid\.?3\b.*?110\s*kw", re.I),
        re.compile(r"\bid\.?3\b.*?150\s*ps", re.I),
        re.compile(r"\bid\.?3\b.*?45\s*kwh", re.I),
    ]),
    EVSpec("Volkswagen", "ID.3", "Pro / Pro Performance", 62.0, 58.0, 426, 150, 204, [
        re.compile(r"\bid\.?3\b.*?\b(?:pro\s+performance|pro\b)", re.I),
        re.compile(r"\bid\.?3\b.*?150\s*kw", re.I),
        re.compile(r"\bid\.?3\b.*?204\s*ps", re.I),
        re.compile(r"\bid\.?3\b.*?58\s*kwh", re.I),
        re.compile(r"\bid\.?3\b", re.I),  # Fallback für ID.3
    ]),
    EVSpec("Volkswagen", "ID.4", "Pure", 55.0, 52.0, 364, 109, 148, [
        re.compile(r"\bid\.?4\b.*?\b(?:pure)\b", re.I),
        re.compile(r"\bid\.?4\b.*?52\s*kwh", re.I),
    ]),
    EVSpec("Volkswagen", "ID.4", "Pro / GTX / 1st", 82.0, 77.0, 522, 150, 204, [
        re.compile(r"\bid\.?4\b.*?\b(?:pro\s*s|pro-s|pro\s+performance|pro|gtx|1st)\b", re.I),
        re.compile(r"\bid\.?4\b.*?77\s*kwh", re.I),
        re.compile(r"\bid\.?4\b", re.I),
    ]),
    EVSpec("Volkswagen", "ID.5", "Pro / GTX", 82.0, 77.0, 534, 150, 204, [
        re.compile(r"\bid\.?5\b", re.I),
    ]),
    EVSpec("Volkswagen", "ID.7", "Pro", 82.0, 77.0, 621, 210, 286, [
        re.compile(r"\bid\.?7\b.*?\b(?:pro\s*s)\b", re.I),
        re.compile(r"\bid\.?7\b", re.I),
    ]),
    EVSpec("Volkswagen", "ID.Buzz", "Pro", 82.0, 77.0, 423, 150, 204, [
        re.compile(r"\bid\.?buzz\b|\be-?buzz\b", re.I),
    ]),
    EVSpec("Volkswagen", "e-Golf", "35.8 kWh", 35.8, 31.5, 231, 100, 136, [
        re.compile(r"\be-golf\b|\bgolf\s+vii?\s+e\b", re.I),
    ]),
    EVSpec("Volkswagen", "e-Up!", "36.8 kWh", 36.8, 32.3, 260, 61, 83, [
        re.compile(r"\be-up\b", re.I),
    ]),

    # --- TESLA ---
    EVSpec("Tesla", "Model 3", "Performance", 78.5, 75.0, 547, 377, 513, [
        re.compile(r"\bmodel\s*3\b.*?\b(?:performance)\b", re.I),
    ]),
    EVSpec("Tesla", "Model 3", "Long Range / Dual Motor", 78.5, 75.0, 602, 366, 498, [
        re.compile(r"\bmodel\s*3\b.*?\b(?:long\s*range|maximale\s*reichweite|dual\s*motor|allrad|awd)\b", re.I),
    ]),
    EVSpec("Tesla", "Model 3", "Standard Range Plus / RWD", 60.0, 57.5, 491, 208, 283, [
        re.compile(r"\bmodel\s*3\b.*?\b(?:standard|range\s*plus|sr\+|rwd|hinterradantrieb)\b", re.I),
        re.compile(r"\bmodel\s*3\b", re.I),  # Fallback
    ]),
    EVSpec("Tesla", "Model Y", "Performance", 78.5, 75.0, 514, 393, 534, [
        re.compile(r"\bmodel\s*y\b.*?\b(?:performance)\b", re.I),
    ]),
    EVSpec("Tesla", "Model Y", "Long Range / Dual Motor", 78.5, 75.0, 533, 378, 514, [
        re.compile(r"\bmodel\s*y\b.*?\b(?:long\s*range|maximale\s*reichweite|dual\s*motor|allrad|awd)\b", re.I),
        re.compile(r"\bmodel\s*y\b", re.I),
    ]),
    EVSpec("Tesla", "Model Y", "Standard / RWD", 60.0, 57.5, 455, 220, 299, [
        re.compile(r"\bmodel\s*y\b.*?\b(?:standard|rwd|hinterradantrieb)\b", re.I),
    ]),

    # --- CUPRA & SKODA & SEAT ---
    EVSpec("Cupra", "Born", "77 kWh (e-Boost)", 82.0, 77.0, 548, 170, 231, [
        re.compile(r"\bborn\b.*?\b(?:77|e-boost\s*77|231\s*ps)\b", re.I),
    ]),
    EVSpec("Cupra", "Born", "58 kWh", 62.0, 58.0, 424, 150, 204, [
        re.compile(r"\bborn\b.*?\b(?:58|150\s*kw|204\s*ps)\b", re.I),
        re.compile(r"\bborn\b", re.I),
    ]),
    EVSpec("Skoda", "Enyaq iV", "50", 55.0, 52.0, 350, 109, 148, [
        re.compile(r"\benyaq\b.*?\b(?:50)\b", re.I),
    ]),
    EVSpec("Skoda", "Enyaq iV", "60", 62.0, 58.0, 400, 132, 179, [
        re.compile(r"\benyaq\b.*?\b(?:60)\b", re.I),
    ]),
    EVSpec("Skoda", "Enyaq iV", "80 / 80x / 85 / RS", 82.0, 77.0, 535, 150, 204, [
        re.compile(r"\benyaq\b.*?\b(?:80|80x|85|rs|coupe)\b", re.I),
        re.compile(r"\benyaq\b", re.I),
    ]),

    # --- MERCEDES-BENZ ---
    EVSpec("Mercedes-Benz", "EQA", "250+", 70.5, 70.5, 530, 140, 190, [
        re.compile(r"\beqa\s*250\+", re.I),
    ]),
    EVSpec("Mercedes-Benz", "EQA", "250 / 300 / 350", 66.5, 66.5, 430, 140, 190, [
        re.compile(r"\beqa\s*(?:250|300|350)\b", re.I),
        re.compile(r"\beqa\b", re.I),
    ]),
    EVSpec("Mercedes-Benz", "EQB", "250 / 300 / 350", 66.5, 66.5, 420, 168, 228, [
        re.compile(r"\beqb\s*(?:250|250\+|300|350)\b", re.I),
        re.compile(r"\beqb\b", re.I),
    ]),
    EVSpec("Mercedes-Benz", "EQE", "300 / 350", 96.0, 89.0, 620, 180, 245, [
        re.compile(r"\beqe\s*(?:300|350)\b", re.I),
        re.compile(r"\beqe\b", re.I),
    ]),
    EVSpec("Mercedes-Benz", "EQC", "400 4MATIC", 85.0, 80.0, 415, 300, 408, [
        re.compile(r"\beqc\s*(?:400)?\b", re.I),
    ]),

    # --- BMW ---
    EVSpec("BMW", "i3", "120 Ah", 42.2, 37.9, 305, 125, 170, [
        re.compile(r"\bi3\s*s?\b.*?\b120\s*ah\b", re.I),
        re.compile(r"\bi3\s*s?\b", re.I),  # Häufigste Version ab 2019
    ]),
    EVSpec("BMW", "i3", "94 Ah", 33.2, 27.2, 255, 125, 170, [
        re.compile(r"\bi3\s*s?\b.*?\b94\s*ah\b", re.I),
    ]),
    EVSpec("BMW", "i3", "60 Ah", 22.0, 18.8, 190, 125, 170, [
        re.compile(r"\bi3\s*s?\b.*?\b60\s*ah\b", re.I),
    ]),
    EVSpec("BMW", "iX3", "80 kWh", 80.0, 74.0, 460, 210, 286, [
        re.compile(r"\bix3\b", re.I),
    ]),
    EVSpec("BMW", "i4", "eDrive35", 70.2, 67.0, 480, 210, 286, [
        re.compile(r"\bi4\b.*?\b(?:edrive35)\b", re.I),
    ]),
    EVSpec("BMW", "i4", "eDrive40 / M50", 83.9, 80.7, 585, 250, 340, [
        re.compile(r"\bi4\b.*?\b(?:edrive40|m50)\b", re.I),
        re.compile(r"\bi4\b", re.I),
    ]),
    EVSpec("BMW", "iX1", "eDrive20 / xDrive30", 66.5, 64.7, 440, 150, 204, [
        re.compile(r"\bix1\b", re.I),
    ]),

    # --- AUDI ---
    EVSpec("Audi", "Q4 e-tron", "35", 55.0, 52.0, 341, 125, 170, [
        re.compile(r"\bq4\b.*?\b(?:35)\b", re.I),
    ]),
    EVSpec("Audi", "Q4 e-tron", "40 / 45 / 50", 82.0, 77.0, 520, 150, 204, [
        re.compile(r"\bq4\b.*?\b(?:40|45|50|55)\b", re.I),
        re.compile(r"\bq4\b", re.I),
    ]),
    EVSpec("Audi", "e-tron / Q8 e-tron", "50 / 55", 95.0, 86.5, 436, 230, 313, [
        re.compile(r"\be-tron\b|\bq8\s+e-tron\b", re.I),
    ]),

    # --- HYUNDAI & KIA ---
    EVSpec("Hyundai", "Ioniq 5", "58 kWh", 58.0, 54.0, 384, 125, 170, [
        re.compile(r"\bioniq\s*5\b.*?\b(?:58)\b", re.I),
    ]),
    EVSpec("Hyundai", "Ioniq 5", "72.6 / 77.4 kWh", 77.4, 74.0, 481, 160, 217, [
        re.compile(r"\bioniq\s*5\b.*?\b(?:72|72\.6|77|77\.4|84)\b", re.I),
        re.compile(r"\bioniq\s*5\b", re.I),
    ]),
    EVSpec("Hyundai", "Kona Elektro", "39.2 kWh", 39.2, 39.2, 305, 100, 136, [
        re.compile(r"\bkona\b.*?\b(?:39|39\.2|100\s*kw|136\s*ps)\b", re.I),
    ]),
    EVSpec("Hyundai", "Kona Elektro", "64 / 65.4 kWh", 64.0, 64.0, 484, 150, 204, [
        re.compile(r"\bkona\b.*?\b(?:64|65\.4|150\s*kw|204\s*ps|218\s*ps)\b", re.I),
        re.compile(r"\bkona\b", re.I),
    ]),
    EVSpec("Kia", "EV6", "58 / 77.4 kWh", 77.4, 74.0, 528, 168, 229, [
        re.compile(r"\bev6\b", re.I),
    ]),
    EVSpec("Kia", "e-Niro / Niro EV", "64.8 kWh", 64.8, 64.8, 460, 150, 204, [
        re.compile(r"\be-niro\b|\bniro\s*ev\b|\bniro\s+elektro\b", re.I),
    ]),

    # --- RENAULT, PEUGEOT, OPEL, FIAT, NISSAN, MG ---
    EVSpec("Renault", "Zoe", "Z.E. 50 (52 kWh)", 54.7, 52.0, 395, 80, 108, [
        re.compile(r"\bzoe\b.*?\b(?:ze\s*50|52|r110|r135)\b", re.I),
        re.compile(r"\bzoe\b", re.I),
    ]),
    EVSpec("Renault", "Zoe", "Z.E. 40 (41 kWh)", 44.1, 41.0, 300, 68, 92, [
        re.compile(r"\bzoe\b.*?\b(?:ze\s*40|41)\b", re.I),
    ]),
    EVSpec("Renault", "Megane E-Tech", "EV60", 60.0, 60.0, 450, 160, 218, [
        re.compile(r"\bmegane\b.*?\b(?:ev60|60\s*kwh|220\s*ps)\b", re.I),
        re.compile(r"\bmegane\b", re.I),
    ]),
    EVSpec("Fiat", "500e", "42 kWh", 42.0, 37.3, 320, 87, 118, [
        re.compile(r"\b500e\b|\bfiat\s*500\s*e\b", re.I),
    ]),
    EVSpec("Peugeot", "e-208 / e-2008", "50 kWh", 50.0, 46.3, 362, 100, 136, [
        re.compile(r"\be-?208\b|\be-?2008\b", re.I),
    ]),
    EVSpec("Opel", "Corsa-e / Mokka-e", "50 kWh", 50.0, 46.3, 353, 100, 136, [
        re.compile(r"\bcorsa-?e\b|\bmokka-?e\b", re.I),
    ]),
    EVSpec("Nissan", "Leaf", "e+ (62 kWh)", 62.0, 59.0, 385, 160, 217, [
        re.compile(r"\bnissan\s*leaf\b.*?\b(?:e\+|62|217\s*ps)\b", re.I),
    ]),
    EVSpec("Nissan", "Leaf", "40 kWh", 40.0, 39.0, 270, 110, 150, [
        re.compile(r"\bnissan\s*leaf\b", re.I),
    ]),
    EVSpec("MG", "MG4", "Standard (51 kWh) / Comfort (64 kWh)", 64.0, 61.7, 450, 150, 204, [
        re.compile(r"\bmg4\b", re.I),
    ]),
    EVSpec("Smart", "#1", "66 kWh", 66.0, 62.0, 440, 200, 272, [
        re.compile(r"\bsmart\s*#1\b", re.I),
    ]),
    EVSpec("Smart", "EQ fortwo / forfour", "17.6 kWh", 17.6, 16.7, 135, 60, 82, [
        re.compile(r"\bsmart\s*eq\b|\bfortwo\s*eq\b|\bforfour\s*eq\b", re.I),
    ]),
    EVSpec("Ora", "03 / Funky Cat", "48 / 63 kWh", 48.0, 45.4, 310, 126, 171, [
        re.compile(r"\bora\s*(?:03|funky\s*cat)\b", re.I),
    ]),
]


def lookup_ev_spec(title: str | None, body: str | None = None) -> Optional[EVSpec]:
    """Sucht in der internen EV-Referenzdatenbank nach dem passenden Modell."""
    text = f"{title or ''} {body or ''}".strip()
    if not text:
        return None
    for spec in _EV_DATABASE:
        if spec.patterns:
            for pat in spec.patterns:
                if pat.search(text):
                    return spec
    return None