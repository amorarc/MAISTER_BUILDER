"""Every part this project has found, kept across the whole build.

A run does not search once. It searches for a wheel, builds the chassis,
searches for a windscreen, builds the cab - and by then it has forgotten the
wheel, because each subconstruction is a fresh agent with a fresh conversation
and the search results scrolled out of it three sub-builds ago. What comes out
is a model whose front half uses curved slopes and whose back half approximates
the same curve out of stacked plates, because the second builder never knew the
first had already found the part.

So the parts a project finds are written down, not remembered. This is that
file: one entry per part number, with what it is and how it joins, accumulated
across every search in every sub-build, and handed back to each new builder as
the palette it already has.

Keyed by part number and nothing else, so a part found by four different
searches is one entry. That matters more than it sounds: the same part arriving
four times is four copies of the same paragraph in a context window that has
better things to hold.
"""

import json
import os
import threading
import time
from functools import lru_cache

from .config import DATA_DIR, OUT_DIR

PALETTE_DIR = OUT_DIR / "palettes"

# The colour scheme, filed alongside the parts under a key no part_id can
# collide with - part numbers are alphanumeric, and this is not.
#
# It is here rather than in a file of its own for the reason the parts are: a
# scene is built by several agents that cannot see each other, and a colour
# decided by the first of them has to reach the rest. Without it the house comes
# out red because red is the first colour anyone thinks of, and so does the car,
# and so does the tree's flowerpot.
SCHEME_KEY = "__scheme__"

# Subconstructions are built at the same time now, and every one of them
# searches for parts, so several threads reach `record` for one project at
# once. Without this each would read the file, add its own finds to what it
# read, and write the whole thing back - so whichever finished last would
# erase every part the others had found, and a reader landing mid-write would
# get half a JSON object and treat the palette as empty. One lock per project,
# and the write goes through a rename, which the filesystem does in one step.
_locks = {}
_guard = threading.Lock()


def _lock(project):
    with _guard:
        return _locks.setdefault(str(project or "default"), threading.Lock())

# How many parts a palette keeps. Past this the oldest go: a builder that has
# seen four hundred parts is not being helped by being shown four hundred parts,
# and the ones it found most recently are the ones it is working with.
MAX_PARTS = 120

# What a palette shows a builder at once. The rest stay on file and still count
# as "already found" - this is the reminder, not the archive.
SUMMARY_LIMIT = 40


def _path(project):
    name = "".join(c for c in str(project or "default")
                   if c.isalnum() or c in "-_") or "default"
    return PALETTE_DIR / f"{name}.json"


def load(project):
    """``{part_id: entry}`` for this project, oldest first."""
    path = _path(project)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parts_only(data):
    """The palette's part entries, without the colour scheme sitting with them."""
    return {k: v for k, v in (data or {}).items() if k != SCHEME_KEY}


@lru_cache(maxsize=1)
def _colour_names():
    """``{code: name}`` from LDConfig.ldr, the LDraw colour definitions.

    Read from the file rather than written out here, so a code the agent uses
    is named by the same authority the renderer resolves it against. An absent
    or unreadable LDConfig costs the scheme its names, not its codes.
    """
    path = DATA_DIR / "parts" / "LDConfig.ldr"
    names = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return names
    for line in text.splitlines():
        fields = line.split()
        # 0 !COLOUR Reddish_Brown CODE 70 VALUE #582A12 EDGE #...
        if len(fields) < 5 or fields[:2] != ["0", "!COLOUR"]:
            continue
        try:
            code = int(fields[fields.index("CODE") + 1])
        except (ValueError, IndexError):
            continue
        names[code] = fields[2].replace("_", " ")
    return names


def colour_name(code):
    """The LDraw name for a colour code, or None."""
    try:
        return _colour_names().get(int(code))
    except (TypeError, ValueError):
        return None


def record_scheme(project, codes):
    """Set this project's colour scheme, if it does not have one yet.

    First writer wins, deliberately. The scheme belongs to the scene, and the
    scene's first object is the one that establishes it - a later object
    overwriting it would leave the objects built before it painted to a scheme
    that no longer exists, which is worse than no scheme at all.
    """
    codes = [int(c) for c in (codes or []) if str(c).strip().lstrip("-").isdigit()]
    if not codes:
        return None

    with _lock(project):
        known = load(project)
        if known.get(SCHEME_KEY):
            return known[SCHEME_KEY].get("codes")
        known[SCHEME_KEY] = {"codes": codes, "at": time.time()}
        _write(project, known)
        return codes


def scheme(project):
    """This project's colour codes, or an empty list."""
    entry = load(project).get(SCHEME_KEY) or {}
    codes = entry.get("codes")
    return list(codes) if isinstance(codes, list) else []


def _entry(row, query=None):
    """One part, reduced to what a builder needs to recognise it again."""
    return {
        "part_id": row.get("part_id"),
        "description": row.get("description"),
        "category": row.get("category"),
        "width_studs": row.get("width_studs"),
        "depth_studs": row.get("depth_studs"),
        "connections": row.get("connections") or [],
        "attaches": row.get("attaches") or row.get("attachment"),
        "found_for": query or None,
        "at": time.time(),
    }


def record(project, rows, query=None):
    """Add parts to this project's palette. Returns how many were new.

    A part already on file keeps its original entry rather than being rewritten,
    so the query that first turned it up - usually the most descriptive one - is
    the one that stays attached to it.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("part_id")]
    if not rows:
        return 0

    # Read, add and write as one step: two builders searching at the same
    # moment must both end up in the file, not the later one only.
    with _lock(project):
        known = load(project)
        colours = known.get(SCHEME_KEY)
        found = _parts_only(known)

        added = 0
        for row in rows:
            key = str(row["part_id"]).lower()
            if key in found:
                continue
            found[key] = _entry(row, query)
            added += 1
        if not added:
            return 0

        # Trimmed over the parts alone: the scheme is one entry that the whole
        # scene depends on, and ageing it out because a builder ran forty
        # searches would repaint every object built after it.
        if len(found) > MAX_PARTS:
            oldest = sorted(found.items(), key=lambda kv: kv[1].get("at") or 0)
            found = dict(oldest[-MAX_PARTS:])

        if colours:
            found[SCHEME_KEY] = colours
        return added if _write(project, found) else 0


def _write(project, data):
    """Replace a project's palette file. True if it landed.

    Written to a temporary file and renamed, which the filesystem does in one
    step - a reader arriving mid-write gets the old palette rather than half of
    the new one. Callers hold the project's lock.
    """
    path = _path(project)
    try:
        PALETTE_DIR.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        return False
    return True


def parts(project, limit=SUMMARY_LIMIT):
    """The palette, most recently found first."""
    entries = sorted(_parts_only(load(project)).values(),
                     key=lambda e: -(e.get("at") or 0))
    return entries[:limit]


def scheme_summary(project):
    """The colour scheme as a line a builder can read, or None."""
    codes = scheme(project)
    if not codes:
        return None
    rendered = ", ".join(
        f"`{code}`" + (f" ({colour_name(code)})" if colour_name(code) else "")
        for code in codes)
    return (f"**This project's colours: {rendered}.** They were settled for the "
            f"whole scene, and every object in it is painted from them - an "
            f"object that picks its own colours is the one that looks like it "
            f"came from somewhere else. Use others only for what genuinely has "
            f"its own colour: foliage, skin, glass, a warning light.")


def summary(project, limit=SUMMARY_LIMIT):
    """The palette as lines a builder can read, or None if it is empty."""
    found = parts(project, limit)
    if not found:
        return scheme_summary(project)
    lines = []
    for entry in found:
        bits = [f"`{entry['part_id']}`", str(entry.get("description") or "").strip()]
        if entry.get("attaches"):
            bits.append(f"- {entry['attaches']}")
        lines.append("- " + " ".join(b for b in bits if b))
    total = len(_parts_only(load(project)))
    # "this project", not "this build": the file outlives a single run, which
    # is the whole point - a follow-up turn should not start from nothing.
    head = (f"{total} part{'' if total == 1 else 's'} "
            f"{'has' if total == 1 else 'have'} already been found "
            f"for this project")
    if total > len(found):
        head += f" (the {len(found)} most recent shown)"

    # The colours first: they apply to every part below, and a builder that
    # reads the list and stops has still read the thing that keeps a scene
    # looking like one scene.
    blocks = [b for b in (scheme_summary(project),
                          head + ":\n" + "\n".join(lines)) if b]
    return "\n\n".join(blocks)


def forget(project):
    """Drop a project's palette - a new build starts with an empty one."""
    try:
        _path(project).unlink(missing_ok=True)
    except OSError:
        pass
