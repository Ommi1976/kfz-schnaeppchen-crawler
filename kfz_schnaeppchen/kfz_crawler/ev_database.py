"""Interne Referenzdatenbank für Elektrofahrzeuge (Akkukapazität Brutto/Netto & WLTP-Reichweite)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


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
    source_name: str = "Hersteller-/WLTP-Referenzdaten"
    source_url: str = ""
    verified: bool = True


@dataclass(frozen=True)
class EVMatch:
    """Nachvollziehbares Ergebnis einer Varianten-Zuordnung."""

    spec: EVSpec
    confidence: float
    matched_pattern: str
    evidence: str


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

    # Cupra Born: Leistung und "e-Boost" reichen nicht zur Bestimmung der
    # Batterie, da sowohl die 58- als auch die 77-kWh-Variante mit 170 kW
    # angeboten wurde. Deshalb nur eindeutige Kapazitätsangaben zuordnen.
    EVSpec("Cupra", "Born", "45 kWh netto / 55 kWh brutto", 55.0, 45.0, 340, 110, 150, [
        re.compile(r"\bborn\b.*?\b(?:45|55)\s*kwh\b", re.I),
    ]),
    EVSpec("Cupra", "Born", "58 kWh netto / 62 kWh brutto", 62.0, 58.0, 425, 150, 204, [
        re.compile(r"\bborn\b.*?\b(?:58|60|62|63)\s*kwh\b", re.I),
    ]),
    EVSpec("Cupra", "Born", "77 kWh netto / 82 kWh brutto", 82.0, 77.0, 548, 170, 231, [
        re.compile(r"\bborn\b.*?\b(?:77|79|82|84)\s*kwh\b", re.I),
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

    # --- MINI ---
    EVSpec("MINI", "Cooper SE", "3-Türer (32.6 kWh)", 32.6, 28.9, 233, 135, 184, [
        re.compile(r"\bmini\b.*?\b(?:cooper\s*se|cooper-se)\b", re.I),
        re.compile(r"\bcooper\s*se\b", re.I),
    ]),
    EVSpec("MINI", "Cooper E", "40.7 kWh (J01)", 40.7, 36.6, 305, 135, 184, [
        re.compile(r"\bmini\b.*?\bcooper\s*e\b", re.I),
    ]),
    EVSpec("MINI", "Countryman SE", "ALL4 (66.5 kWh)", 66.5, 64.7, 433, 230, 313, [
        re.compile(r"\bcountryman\s*(?:se|all4)\b", re.I),
    ]),
    EVSpec("MINI", "Countryman E", "66.5 kWh", 66.5, 64.7, 462, 150, 204, [
        re.compile(r"\bcountryman\s*e\b", re.I),
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

    # --- RENAULT & DACIA ---
    EVSpec("Renault", "Scenic E-Tech", "EV87 (87 kWh)", 92.0, 87.0, 625, 160, 218, [
        re.compile(r"\bscenic\b.*?\b(?:ev87|87\s*kwh|220\s*ps)\b", re.I),
        re.compile(r"\bscenic\b", re.I),
    ]),
    EVSpec("Renault", "Scenic E-Tech", "EV60 (60 kWh)", 65.0, 60.0, 430, 125, 170, [
        re.compile(r"\bscenic\b.*?\b(?:ev60|60\s*kwh|170\s*ps)\b", re.I),
    ]),
    EVSpec("Renault", "Megane E-Tech", "EV60 (60 kWh)", 65.0, 60.0, 450, 160, 218, [
        re.compile(r"\bmegane\b.*?\b(?:ev60|60\s*kwh|220\s*ps)\b", re.I),
        re.compile(r"\bmegane\b", re.I),
    ]),
    EVSpec("Renault", "Megane E-Tech", "EV40 (40 kWh)", 45.0, 40.0, 300, 96, 130, [
        re.compile(r"\bmegane\b.*?\b(?:ev40|40\s*kwh|130\s*ps)\b", re.I),
    ]),
    EVSpec("Renault", "Zoe", "Z.E. 50 (52 kWh)", 54.7, 52.0, 395, 80, 108, [
        re.compile(r"\bzoe\b.*?\b(?:ze\s*50|52|r110|r135)\b", re.I),
        re.compile(r"\bzoe\b", re.I),
    ]),
    EVSpec("Renault", "Zoe", "Z.E. 40 (41 kWh)", 44.1, 41.0, 300, 68, 92, [
        re.compile(r"\bzoe\b.*?\b(?:ze\s*40|41)\b", re.I),
    ]),
    EVSpec("Renault", "5 E-Tech", "52 kWh", 55.0, 52.0, 410, 110, 150, [
        re.compile(r"\brenault\s*5\b|\br5\s*e-tech\b", re.I),
    ]),
    EVSpec("Dacia", "Spring", "26.8 kWh", 26.8, 25.0, 230, 33, 45, [
        re.compile(r"\bspring\b|\bdacia\s+spring\b", re.I),
    ]),

    # --- PEUGEOT, OPEL, CITROEN, FIAT, JEEP, ALFA ---
    EVSpec("Peugeot", "e-3008 / e-5008", "Long Range (98 kWh)", 103.0, 98.0, 700, 170, 231, [
        re.compile(r"\be-?(?:3008|5008)\b.*?\b(?:long\s*range|98\s*kwh|700\s*km)\b", re.I),
    ]),
    EVSpec("Peugeot", "e-3008 / e-5008", "Standard (73 kWh)", 77.0, 73.0, 525, 157, 213, [
        re.compile(r"\be-?(?:3008|5008)\b", re.I),
    ]),
    EVSpec("Peugeot", "e-308", "54 kWh", 54.0, 51.0, 410, 115, 156, [
        re.compile(r"\be-?308\b", re.I),
    ]),
    EVSpec("Peugeot", "e-208 / e-2008", "50 / 54 kWh", 54.0, 51.0, 400, 115, 156, [
        re.compile(r"\be-?208\b|\be-?2008\b", re.I),
    ]),
    EVSpec("Opel", "Grandland Electric", "73 / 82 / 98 kWh", 98.0, 98.0, 700, 157, 213, [
        re.compile(r"\bgrandland\s*electric\b|\bgrandland\s*e\b", re.I),
    ]),
    EVSpec("Opel", "Astra Electric", "54 kWh", 54.0, 51.0, 418, 115, 156, [
        re.compile(r"\bastra\s*electric\b|\bastra-?e\b", re.I),
    ]),
    EVSpec("Opel", "Corsa-e / Mokka-e", "50 / 54 kWh", 54.0, 51.0, 400, 100, 136, [
        re.compile(r"\bcorsa-?e\b|\bmokka-?e\b|\bcorsa\s*electric\b|\bmokka\s*electric\b", re.I),
    ]),
    EVSpec("Fiat", "600e", "54 kWh", 54.0, 51.0, 409, 115, 156, [
        re.compile(r"\b600e\b|\bfiat\s*600\s*e\b", re.I),
    ]),
    EVSpec("Fiat", "500e", "42 kWh", 42.0, 37.3, 320, 87, 118, [
        re.compile(r"\b500e\b|\bfiat\s*500\s*e\b", re.I),
    ]),
    EVSpec("Jeep", "Avenger", "54 kWh", 54.0, 51.0, 400, 115, 156, [
        re.compile(r"\bavenger\b|\bjeep\s*avenger\b", re.I),
    ]),
    EVSpec("Alfa Romeo", "Junior Elettrica", "54 kWh", 54.0, 51.0, 410, 115, 156, [
        re.compile(r"\bjunior\s*elettrica\b|\bmilano\s*elettrica\b", re.I),
    ]),
    EVSpec("Citroen", "e-C4 / e-C4 X", "50 / 54 kWh", 54.0, 51.0, 420, 100, 136, [
        re.compile(r"\be-?c4\b", re.I),
    ]),
    EVSpec("Citroen", "e-C3", "44 kWh (LFP)", 44.0, 44.0, 320, 83, 113, [
        re.compile(r"\be-?c3\b", re.I),
    ]),

    # --- NISSAN, TOYOTA, SUBARU, LEXUS ---
    EVSpec("Nissan", "Ariya", "87 kWh", 91.0, 87.0, 533, 178, 242, [
        re.compile(r"\bariya\b.*?\b(?:87|e-4orce|evolve|242\s*ps)\b", re.I),
        re.compile(r"\bariya\b", re.I),
    ]),
    EVSpec("Nissan", "Ariya", "63 kWh", 65.0, 63.0, 403, 160, 218, [
        re.compile(r"\bariya\b.*?\b(?:63|218\s*ps)\b", re.I),
    ]),
    EVSpec("Nissan", "Leaf", "e+ (62 kWh)", 62.0, 59.0, 385, 160, 217, [
        re.compile(r"\bnissan\s*leaf\b.*?\b(?:e\+|62|217\s*ps)\b", re.I),
    ]),
    EVSpec("Nissan", "Leaf", "40 kWh", 40.0, 39.0, 270, 110, 150, [
        re.compile(r"\bnissan\s*leaf\b", re.I),
    ]),
    EVSpec("Toyota", "bZ4X", "71.4 kWh", 71.4, 64.0, 513, 150, 204, [
        re.compile(r"\bbz4x\b", re.I),
    ]),
    EVSpec("Subaru", "Solterra", "71.4 kWh", 71.4, 64.0, 465, 160, 218, [
        re.compile(r"\bsolterra\b", re.I),
    ]),
    EVSpec("Lexus", "RZ 450e", "71.4 kWh", 71.4, 64.0, 440, 230, 313, [
        re.compile(r"\brz\s*450e\b|\brz\b", re.I),
    ]),
    EVSpec("Lexus", "UX 300e", "72.8 kWh", 72.8, 64.0, 450, 150, 204, [
        re.compile(r"\bux\s*300e\b.*?\b72\b", re.I),
    ]),

    # --- PORSCHE & AUDI SPORT ---
    EVSpec("Porsche", "Taycan", "Performance Plus (93.4 / 105 kWh)", 93.4, 83.7, 505, 280, 380, [
        re.compile(r"\btaycan\b.*?\b(?:plus|4s|turbo|gts|105|93)\b", re.I),
        re.compile(r"\btaycan\b", re.I),
    ]),
    EVSpec("Porsche", "Taycan", "Performance (79.2 / 89 kWh)", 79.2, 71.0, 430, 240, 326, [
        re.compile(r"\btaycan\b.*?\b(?:79|89)\b", re.I),
    ]),
    EVSpec("Audi", "Q6 e-tron", "100 kWh (800V)", 100.0, 94.9, 625, 285, 387, [
        re.compile(r"\bq6\s*e-tron\b|\bq6\b", re.I),
    ]),
    EVSpec("Audi", "e-tron GT", "93.4 / 105 kWh", 93.4, 85.0, 488, 350, 476, [
        re.compile(r"\be-tron\s*gt\b|\brs\s*e-tron\s*gt\b", re.I),
    ]),

    # --- GENESIS ---
    EVSpec("Genesis", "GV60", "77.4 kWh (800V)", 77.4, 74.0, 517, 168, 229, [
        re.compile(r"\bgv60\b", re.I),
    ]),
    EVSpec("Genesis", "Electrified GV70", "77.4 kWh", 77.4, 74.0, 455, 360, 490, [
        re.compile(r"\bgv70\b", re.I),
    ]),
    EVSpec("Genesis", "Electrified G80", "87.2 kWh", 87.2, 87.2, 520, 272, 370, [
        re.compile(r"\bg80\b", re.I),
    ]),

    # --- FORD ---
    EVSpec("Ford", "Mustang Mach-E", "Extended Range (98.7 kWh)", 98.7, 91.0, 600, 216, 294, [
        re.compile(r"\bmach-?e\b.*?\b(?:er|extended|98|91|awd|4x)\b", re.I),
    ]),
    EVSpec("Ford", "Mustang Mach-E", "Standard Range (75.7 kWh)", 75.7, 70.0, 440, 198, 269, [
        re.compile(r"\bmach-?e\b", re.I),
    ]),
    EVSpec("Ford", "Explorer Electric", "Extended (77 / 79 kWh)", 82.0, 77.0, 602, 210, 286, [
        re.compile(r"\bexplorer\s*electric\b|\bexplorer\s*ev\b", re.I),
    ]),
    EVSpec("Ford", "Capri Electric", "Extended (77 / 79 kWh)", 82.0, 77.0, 627, 210, 286, [
        re.compile(r"\bcapri\s*electric\b|\bcapri\s*ev\b", re.I),
    ]),

    # --- MG & SMART ---
    EVSpec("MG", "MG4", "Trophy Extended Range (77 kWh)", 77.0, 74.4, 520, 180, 245, [
        re.compile(r"\bmg4\b.*?\b(?:extended|77|trophy)\b", re.I),
    ]),
    EVSpec("MG", "MG4", "Comfort / Luxury (64 kWh)", 64.0, 61.7, 450, 150, 204, [
        re.compile(r"\bmg4\b.*?\b(?:comfort|luxury|64)\b", re.I),
        re.compile(r"\bmg4\b", re.I),
    ]),
    EVSpec("MG", "MG4", "Standard (51 kWh)", 51.0, 50.8, 350, 125, 170, [
        re.compile(r"\bmg4\b.*?\b(?:standard|51)\b", re.I),
    ]),
    EVSpec("MG", "MG5", "Maxi (61.1 kWh)", 61.1, 57.4, 400, 115, 156, [
        re.compile(r"\bmg5\b", re.I),
    ]),
    EVSpec("MG", "ZS EV", "Long Range (70 kWh)", 72.6, 68.3, 440, 115, 156, [
        re.compile(r"\bzs\s*ev\b.*?\b(?:long|70|72)\b", re.I),
        re.compile(r"\bzs\s*ev\b", re.I),
    ]),
    EVSpec("MG", "Marvel R", "70 kWh", 70.0, 65.0, 402, 132, 180, [
        re.compile(r"\bmarvel\s*r\b", re.I),
    ]),
    EVSpec("Smart", "#1", "Pro+ / Premium / Brabus (66 kWh)", 66.0, 62.0, 440, 200, 272, [
        re.compile(r"\bsmart\s*#1\b.*?\b(?:premium|pro\+|brabus|66)\b", re.I),
        re.compile(r"\bsmart\s*#1\b", re.I),
    ]),
    EVSpec("Smart", "#1", "Pro (49 kWh LFP)", 49.0, 47.0, 310, 200, 272, [
        re.compile(r"\bsmart\s*#1\b.*?\b(?:pro\b(?!.*?pro\+)|49)\b", re.I),
    ]),
    EVSpec("Smart", "#3", "Pro+ / Premium / Brabus (66 kWh)", 66.0, 62.0, 455, 200, 272, [
        re.compile(r"\bsmart\s*#3\b", re.I),
    ]),
    EVSpec("Smart", "EQ fortwo / forfour", "17.6 kWh", 17.6, 16.7, 135, 60, 82, [
        re.compile(r"\bsmart\s*eq\b|\bfortwo\s*eq\b|\bforfour\s*eq\b", re.I),
    ]),

    # --- BYD ---
    EVSpec("BYD", "Dolphin", "Surf / Active / Boost (43.2 / 44.9 kWh)", 44.9, 43.2, 310, 115, 156, [
        re.compile(r"\b(?:byd\b.*?\bdolphin\b.*?\b(?:surf|active|boost)\b|dolphin\s*surf\b|\bsurf\s*comfort\b)", re.I),
    ]),
    EVSpec("BYD", "Dolphin", "Comfort / Design (60.4 kWh)", 60.4, 60.4, 427, 150, 204, [
        re.compile(r"\b(?:byd\b.*?\bdolphin\b|\bdolphin\b)", re.I),
    ]),
    EVSpec("BYD", "Atto 3", "60.5 kWh", 60.5, 60.5, 420, 150, 204, [
        re.compile(r"\b(?:byd\b.*?\batto\s*3\b|\batto\s*3\b)", re.I),
    ]),
    EVSpec("BYD", "Seal", "Design / Excellence (82.5 kWh)", 82.5, 82.5, 570, 230, 313, [
        re.compile(r"\b(?:byd\b.*?\bseal\b|\bseal\s+rwd|\bseal\s+awd)\b", re.I),
    ]),
    EVSpec("BYD", "Seal U", "71.8 / 87 kWh", 87.0, 87.0, 500, 160, 218, [
        re.compile(r"\bseal\s*u\b", re.I),
    ]),
    EVSpec("BYD", "Han", "85.4 kWh", 85.4, 85.4, 521, 380, 517, [
        re.compile(r"\bbyd\s*han\b|\bhan\s*ev\b", re.I),
    ]),
    EVSpec("BYD", "Tang", "86.4 / 108.8 kWh", 108.8, 108.8, 530, 380, 517, [
        re.compile(r"\bbyd\s*tang\b|\btang\s*ev\b", re.I),
    ]),

    # --- NIO, FISKER, LUCID, VINFAST, ORA ---
    EVSpec("Nio", "ET5 / ET5 Touring", "100 kWh", 100.0, 90.0, 590, 360, 490, [
        re.compile(r"\bet5\b.*?\b100\b", re.I),
    ]),
    EVSpec("Nio", "ET5 / ET5 Touring", "75 kWh", 75.0, 70.0, 456, 360, 490, [
        re.compile(r"\bet5\b", re.I),
    ]),
    EVSpec("Nio", "ET7", "75 / 100 kWh", 100.0, 90.0, 580, 480, 653, [
        re.compile(r"\bet7\b", re.I),
    ]),
    EVSpec("Nio", "EL6 / EL7 / EL8", "75 / 100 kWh", 100.0, 90.0, 529, 360, 490, [
        re.compile(r"\b(?:el6|el7|el8)\b", re.I),
    ]),
    EVSpec("Fisker", "Ocean", "Extreme / One / Ultra (113 kWh)", 113.0, 106.0, 707, 420, 571, [
        re.compile(r"\bocean\b|\bfisker\b", re.I),
    ]),
    EVSpec("Lucid", "Air", "Pure / Touring / GT (88-118 kWh)", 112.0, 112.0, 725, 456, 620, [
        re.compile(r"\blucid\b|\blucid\s*air\b", re.I),
    ]),
    EVSpec("VinFast", "VF 8", "ECO / PLUS (87.7 kWh)", 87.7, 87.7, 471, 260, 353, [
        re.compile(r"\bvf\s*8\b|\bvinfast\b", re.I),
    ]),
    EVSpec("Ora", "03 / Funky Cat", "400 (63 kWh)", 63.0, 59.3, 420, 126, 171, [
        re.compile(r"\bora\s*(?:03|funky\s*cat)\b.*?\b(?:400|63|gt)\b", re.I),
    ]),
    EVSpec("Ora", "03 / Funky Cat", "300 (48 kWh LFP)", 48.0, 45.4, 310, 126, 171, [
        re.compile(r"\bora\s*(?:03|funky\s*cat)\b", re.I),
    ]),

    # --- VOLVO & POLESTAR ---
    EVSpec("Volvo", "EX30", "Single Motor Extended Range / Twin (69 kWh)", 69.0, 64.0, 476, 200, 272, [
        re.compile(r"\bex30\b.*?\b(?:extended|twin|plus|ultra|69)\b", re.I),
        re.compile(r"\bex30\b", re.I),
    ]),
    EVSpec("Volvo", "EX30", "Single Motor (51 kWh LFP)", 51.0, 49.0, 344, 200, 272, [
        re.compile(r"\bex30\b.*?\b(?:single\s+motor\b(?!.*?extended)|core\b(?!.*?extended)|51\s*kwh)\b", re.I),
    ]),
    EVSpec("Volvo", "XC40 / EX40 / C40 / EC40", "Extended / Twin (78 / 82 kWh)", 82.0, 79.0, 575, 185, 252, [
        re.compile(r"\b(?:xc40|ex40|c40|ec40)\b.*?\b(?:extended|twin|82|78)\b", re.I),
        re.compile(r"\b(?:xc40|ex40|c40|ec40)\b", re.I),
    ]),
    EVSpec("Volvo", "XC40 / EX40 / C40 / EC40", "Standard Range (69 kWh)", 69.0, 67.0, 425, 170, 231, [
        re.compile(r"\b(?:xc40|ex40|c40|ec40)\b.*?\b(?:standard|69)\b", re.I),
    ]),
    EVSpec("Polestar", "2", "Long Range (78 / 82 kWh)", 82.0, 79.0, 551, 220, 299, [
        re.compile(r"\bpolestar\s*2\b.*?\b(?:long\s*range|dual\s*motor|82|78)\b", re.I),
        re.compile(r"\bpolestar\s*2\b", re.I),
    ]),
    EVSpec("Polestar", "2", "Standard Range (69 kWh)", 69.0, 67.0, 478, 170, 231, [
        re.compile(r"\bpolestar\s*2\b.*?\b(?:standard\s*range|69\s*kwh)\b", re.I),
    ]),
    EVSpec("Polestar", "3 / 4", "100 / 111 kWh", 111.0, 107.0, 610, 360, 490, [
        re.compile(r"\bpolestar\s*(?:3|4)\b", re.I),
    ]),

    # --- KIA & HYUNDAI NEU ---
    EVSpec("Kia", "EV3", "Long Range (81.4 kWh)", 81.4, 81.4, 605, 150, 204, [
        re.compile(r"\bev3\b.*?\b(?:long|81|605)\b", re.I),
        re.compile(r"\bev3\b", re.I),
    ]),
    EVSpec("Kia", "EV3", "Standard (58.3 kWh)", 58.3, 58.3, 436, 150, 204, [
        re.compile(r"\bev3\b.*?\b(?:standard|58\s*kwh)\b", re.I),
    ]),
    EVSpec("Kia", "EV9", "99.8 kWh (800V)", 99.8, 99.8, 563, 283, 385, [
        re.compile(r"\bev9\b", re.I),
    ]),
    EVSpec("Dacia", "Spring", "26.8 kWh", 26.8, 25.0, 230, 33, 45, [
        re.compile(r"\bspring\b|\bdacia\s+spring\b", re.I),
    ]),
]


_CAPACITY_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*k\s*wh\b",
    re.IGNORECASE,
)


def _capacity_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _CAPACITY_RE.finditer(text):
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if 15.0 <= value <= 130.0:
            values.append(value)
    return values


def lookup_ev_spec_match(
    title: str | None,
    body: str | None = None,
    power_ps: int | None = None,
    power_kw: int | None = None,
) -> Optional[EVMatch]:
    """Ordnet ein EV einer Variante zu und verwirft mehrdeutige Treffer.

    Explizite Kapazitäten und Leistung haben Vorrang vor generischen
    Modell-Fallbacks. Nahezu gleich bewertete, widersprüchliche Varianten werden
    bewusst als unbekannt behandelt, statt einen präzise wirkenden Fantasiewert
    zu liefern.
    """
    parts = [str(title or ""), str(body or "")]
    if power_ps:
        parts.append(f"{power_ps} ps {power_ps}ps")
        if not power_kw:
            kw = round(power_ps / 1.35962)
            parts.append(f"{kw} kw {kw}kw")
    if power_kw:
        parts.append(f"{power_kw} kw {power_kw}kw")
        if not power_ps:
            ps = round(power_kw * 1.35962)
            parts.append(f"{ps} ps {ps}ps")

    text = " ".join(parts).strip()
    if not text:
        return None
    title_text = str(title or "")
    capacities = _capacity_values(text)
    ranked: list[tuple[float, EVSpec, re.Pattern, list[str]]] = []
    for spec in _EV_DATABASE:
        for pat in spec.patterns or []:
            match = pat.search(text)
            if not match:
                continue
            reasons: list[str] = []
            score = min(24.0, len(pat.pattern) / 5.0)
            if pat.search(title_text):
                score += 25.0
                reasons.append("Modellmuster im Titel")
            else:
                reasons.append("Modellmuster im Beschreibungstext")

            if capacities:
                delta = min(
                    abs(value - capacity)
                    for value in capacities
                    for capacity in (spec.battery_gross_kwh, spec.battery_net_kwh)
                )
                if delta <= 1.5:
                    score += 80.0
                    reasons.append(f"Kapazität passend ({capacities[0]:g} kWh)")
                elif delta <= 4.0:
                    score += 25.0
                    reasons.append("Kapazität ungefähr passend")
                else:
                    score -= 45.0
                    reasons.append("Kapazität widersprüchlich")

            effective_ps = power_ps or (round(power_kw * 1.35962) if power_kw else None)
            if effective_ps and spec.power_ps:
                delta_ps = abs(effective_ps - spec.power_ps)
                if delta_ps <= 3:
                    score += 28.0
                    reasons.append("Leistung passend")
                elif delta_ps <= 15:
                    score += 8.0
                elif delta_ps >= 35:
                    score -= 15.0
            ranked.append((score, spec, pat, reasons))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_spec, best_pattern, best_reasons = ranked[0]
    sibling_capacities = {
        spec.battery_gross_kwh
        for spec in _EV_DATABASE
        if spec.make == best_spec.make and spec.model == best_spec.model
    }
    if (
        not capacities
        and not power_ps
        and not power_kw
        and len(sibling_capacities) > 1
        and len(best_pattern.pattern) < 30
    ):
        return None
    for next_score, next_spec, _, _ in ranked[1:]:
        if best_score - next_score > 6.0:
            break
        if (
            best_spec.model == next_spec.model
            and abs(best_spec.battery_gross_kwh - next_spec.battery_gross_kwh) > 2.0
        ):
            return None

    confidence = 0.78
    if capacities and any("Kapazität passend" in reason for reason in best_reasons):
        confidence = 0.97
    elif any("Leistung passend" in reason for reason in best_reasons):
        confidence = 0.91
    elif best_pattern.search(title_text):
        confidence = 0.86
    return EVMatch(
        spec=best_spec,
        confidence=confidence,
        matched_pattern=best_pattern.pattern,
        evidence="; ".join(best_reasons),
    )


def lookup_ev_spec(
    title: str | None,
    body: str | None = None,
    power_ps: int | None = None,
    power_kw: int | None = None,
) -> Optional[EVSpec]:
    """Rückwärtskompatible Kurzform der Varianten-Zuordnung."""
    match = lookup_ev_spec_match(title, body, power_ps=power_ps, power_kw=power_kw)
    if match:
        return match.spec
    return None
