"""Trusted commands staged by setuptools from tools/ (one implementation)."""
from ..assets import kit_root

# Editable development resolves only the kit-owned tools directory. Wheels
# contain these modules here and do not consult the source tree at all.
if not (kit_root() / "manifest.json").is_file():
    __path__.append(str(kit_root() / "tools"))
