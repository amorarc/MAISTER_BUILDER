"""
Provides an LDraw library root for the validators.

The validators search <root>/parts, <root>/parts/s, <root>/p, <root>/p/48 and
<root>/p/8. This project stores a merged library in data/lego_pieces, which
already contains the s/, 48/ and 8/ subdirectories, so a root with "parts" and
"p" both pointing at it resolves every reference.
"""

from .config import DATA_DIR, LIBRARY_ROOT, PARTS_DIR

LDCONFIG_SOURCE = DATA_DIR / "parts" / "LDConfig.ldr"


def ensure_library_root():
    """Create (once) and return the shim library root. None if parts are missing."""
    if not PARTS_DIR.is_dir():
        return None

    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("parts", "p"):
        link = LIBRARY_ROOT / name
        if link.is_symlink():
            if link.resolve() == PARTS_DIR.resolve():
                continue
            link.unlink()
        elif link.exists():
            continue  # a real directory is already there; leave it alone
        link.symlink_to(PARTS_DIR, target_is_directory=True)

    # Renderers (LeoCAD, LDView, LDCad) resolve colour codes through this file.
    # Without it every part renders in a default grey.
    if LDCONFIG_SOURCE.is_file():
        for name in ("LDConfig.ldr", "ldconfig.ldr"):
            link = LIBRARY_ROOT / name
            if link.is_symlink() or link.exists():
                continue
            link.symlink_to(LDCONFIG_SOURCE)

    return LIBRARY_ROOT
