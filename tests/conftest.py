import sys
from pathlib import Path

# kfz_schnaeppchen-Verzeichnis zu sys.path hinzufügen
repo_root = Path(__file__).resolve().parent.parent
addon_dir = repo_root / "kfz_schnaeppchen"
if str(addon_dir) not in sys.path:
    sys.path.insert(0, str(addon_dir))
