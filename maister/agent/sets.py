"""Access to the official-model (OMR) set library.

``data/ldraw_omr_sets/`` holds 1,801 real LEGO models as ``.mpd`` files, with
``metadata.csv`` giving theme, year and piece counts for each, and
``data/parts/part_set_usage.csv`` recording which parts each set uses.

A set is identified by its **set number** ("10030-1"). 195 of the 1,463 sets
publish more than one model - alternate builds, individual train cars - so a set
number can map to several files and lookups return the full list.
"""

import csv
import re
from collections import defaultdict
from functools import lru_cache

from .config import OMR_SETS_DIR, PART_SET_USAGE, SETS_METADATA

_INT_FIELDS = ("total_pieces", "unique_pieces", "unique_pieces_by_color")


@lru_cache(maxsize=1)
def load_sets():
    """Every model file, as a metadata row with numeric fields coerced."""
    if not SETS_METADATA.is_file():
        return []
    rows = []
    with open(SETS_METADATA, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for field in _INT_FIELDS:
                try:
                    row[field] = int(row.get(field) or 0)
                except ValueError:
                    row[field] = 0
            row["year"] = (row.get("year") or "").strip()
            rows.append(row)
    return rows


@lru_cache(maxsize=1)
def _by_set_number():
    index = defaultdict(list)
    for row in load_sets():
        index[(row.get("set_number") or "").lower()].append(row)
    return dict(index)


@lru_cache(maxsize=1)
def _by_file_name():
    return {(r.get("file_name") or "").lower(): r for r in load_sets()}


@lru_cache(maxsize=1)
def load_usage():
    """``{set_id: [(part_id, quantity), ...]}``, most-used part first."""
    if not PART_SET_USAGE.is_file():
        return {}
    per_set = defaultdict(list)
    with open(PART_SET_USAGE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                quantity = int(row.get("quantity") or 0)
            except ValueError:
                quantity = 0
            per_set[row.get("set_id")].append((row.get("part_id"), quantity))
    return {k: sorted(v, key=lambda p: -p[1]) for k, v in per_set.items()}


@lru_cache(maxsize=1)
def part_themes():
    """``{part_id: [(theme, set count), ...]}`` - where a part actually shows up.

    Useful context for part search: "minifig cape" is much easier to place once
    the index knows the part appears in Castle and Star Wars sets.
    """
    theme_of = {r["set_id"]: (r.get("theme") or "").strip() for r in load_sets()}
    counts = defaultdict(lambda: defaultdict(int))
    for set_id, parts in load_usage().items():
        theme = theme_of.get(set_id)
        if not theme:
            continue
        for part_id, _ in parts:
            counts[part_id][theme] += 1
    return {p: sorted(t.items(), key=lambda kv: -kv[1]) for p, t in counts.items()}


def resolve(identifier):
    """Model rows for a set number or a model file name. Empty list if unknown."""
    key = (identifier or "").strip().lower()
    if not key:
        return []
    if key in _by_set_number():
        return _by_set_number()[key]
    if not key.endswith(".mpd") and f"{key}.mpd" in _by_file_name():
        key = f"{key}.mpd"
    row = _by_file_name().get(key)
    return [row] if row else []


def model_path(row):
    return OMR_SETS_DIR / row["file_name"]


def summarize(row):
    """The fields worth showing the agent for one model."""
    return {
        "set_number": row.get("set_number"),
        "set_name": row.get("set_name"),
        "theme": row.get("theme"),
        "year": row.get("year"),
        "total_pieces": row.get("total_pieces"),
        "unique_pieces": row.get("unique_pieces"),
        "model_file": row.get("file_name"),
    }


# "~Moved to 2654a" - a retired number redirecting to its replacement
_MOVED_RE = re.compile(r"^~moved to\s+(\S+)", re.IGNORECASE)


def top_parts(row, limit=15):
    """The parts a set uses most, as a list the agent could actually build from.

    Raw usage data is not that list. It contains retired part numbers that only
    redirect elsewhere, and ``~``-prefixed subparts - internal geometry that
    LDraw assembles into real parts and that cannot be placed on its own. Both
    are resolved or dropped here, and quantities are merged when two numbers
    turn out to be the same element.
    """
    from . import catalog

    by_id = {(r.get("part_id") or ""): r for r in catalog.load_catalog()}

    merged, order = {}, []
    for part_id, quantity in load_usage().get(row.get("set_id"), []):
        entry = by_id.get(part_id)
        description = (entry or {}).get("description") or ""

        moved = _MOVED_RE.match(description)
        if moved:
            target = moved.group(1).removesuffix(".dat")
            entry = by_id.get(target)
            if entry is None:
                continue
            part_id, description = target, entry.get("description") or ""

        if description.startswith("~"):  # a subpart, not a build element
            continue

        if part_id not in merged:
            merged[part_id] = {"part_id": part_id, "description": description,
                               "quantity": 0}
            order.append(part_id)
        merged[part_id]["quantity"] += quantity

    ranked = sorted((merged[p] for p in order), key=lambda p: -p["quantity"])
    return ranked[:limit]


def matches_filters(row, theme=None, year_min=None, year_max=None,
                    min_pieces=None, max_pieces=None):
    if theme and theme.lower() not in (row.get("theme") or "").lower():
        return False
    if year_min is not None or year_max is not None:
        try:
            year = int(row.get("year") or 0)
        except ValueError:
            year = 0
        # a set with no recorded year cannot satisfy a year filter
        if not year:
            return False
        if year_min is not None and year < year_min:
            return False
        if year_max is not None and year > year_max:
            return False
    pieces = row.get("total_pieces") or 0
    if min_pieces is not None and pieces < min_pieces:
        return False
    if max_pieces is not None and pieces > max_pieces:
        return False
    return True


# --------------------------------------------------------------------------
# Browsing
#
# The agent reaches the corpus by semantic search, which is right for "a small
# medieval house with a sloped roof" and useless for "show me what is in here".
# A person wants the second: the shelf, sorted, filtered, and paged. This is
# that view - the same shape as catalog.browse so the gallery in the app can be
# the parts gallery with a different noun.
# --------------------------------------------------------------------------

BROWSE_SORTS = ("name", "pieces", "year", "theme", "number")


def _terms(query):
    return [t for t in str(query or "").lower().split() if t]


def _haystack(row):
    return " ".join(str(row.get(k) or "").lower()
                    for k in ("set_number", "set_name", "theme", "year"))


def themes(min_sets=1):
    """Every theme with a set in the corpus, commonest first."""
    counts = {}
    for row in load_sets():
        name = (row.get("theme") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [{"theme": name, "sets": n}
            for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if n >= min_sets]


def browse(query="", theme=None, min_pieces=None, max_pieces=None,
           year_min=None, year_max=None, sort="name", limit=48, offset=0):
    """A page of the set corpus, plus how many matched in total."""
    terms = _terms(query)
    wanted_theme = (theme or "").strip().lower()

    found = []
    for row in load_sets():
        if wanted_theme and wanted_theme not in (row.get("theme") or "").lower():
            continue
        if not matches_filters(row, year_min=year_min, year_max=year_max,
                               min_pieces=min_pieces, max_pieces=max_pieces):
            continue
        if terms:
            hay = _haystack(row)
            if not all(t in hay for t in terms):
                continue
        found.append(row)

    def pieces(row):
        try:
            return int(row.get("total_pieces") or 0)
        except (TypeError, ValueError):
            return 0

    def year(row):
        try:
            return int(row.get("year") or 0)
        except (TypeError, ValueError):
            return 0

    keys = {
        "name": lambda r: (str(r.get("set_name") or "").lower(),),
        "pieces": lambda r: (-pieces(r), str(r.get("set_name") or "").lower()),
        "year": lambda r: (-year(r), str(r.get("set_name") or "").lower()),
        "theme": lambda r: (str(r.get("theme") or "").lower(),
                            str(r.get("set_name") or "").lower()),
        "number": lambda r: (str(r.get("set_number") or "").lower(),),
    }
    found.sort(key=keys.get(sort, keys["name"]))

    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 48), 200))
    page = found[offset:offset + limit]
    return {
        "total": len(found),
        "offset": offset,
        "limit": limit,
        "sets": [summarize(row) for row in page],
    }
