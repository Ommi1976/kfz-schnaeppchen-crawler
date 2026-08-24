"""Portal-Scraper. Jedes Portal implementiert BasePortal.search()."""

from __future__ import annotations

from typing import Dict, Type

from .base import BasePortal
from .autoscout24 import AutoScout24
from .kleinanzeigen import Kleinanzeigen
from .mobile_de import MobileDe
from .heycar import Heycar
from .autouncle import AutoUncle

# Schlüssel müssen mit den Keys in config.yaml -> portals übereinstimmen.
REGISTRY: Dict[str, Type[BasePortal]] = {
    "autoscout24": AutoScout24,
    "kleinanzeigen": Kleinanzeigen,
    "mobile_de": MobileDe,
    "heycar": Heycar,
    "autouncle": AutoUncle,
}
