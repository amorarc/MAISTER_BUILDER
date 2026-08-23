"""Search and inspection over data/parts/parts_catalog.csv."""

import csv
import re
from functools import lru_cache
from pathlib import Path

from .config import HELD_PARTS, PART_ROTATION, PARTS_CATALOG

STUD_PITCH = 20.0


# --------------------------------------------------------------------------
# What a minifigure can hold
#
# Built by maister/database_creation/build_minifig_grips.py from the sets that
# hold these parts. It travels with the part rather than living in a prompt,
# because "this is a thing a minifigure holds, and here is where in the hand it
# goes" is a property of the sword — and a builder who has just chosen a sword
# should not have to know to go and ask a second question about it.
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _grips():
    """part_id -> how a minifigure holds it."""
    if not HELD_PARTS.is_file():
        return {}
    with open(HELD_PARTS, newline="", encoding="utf-8") as fh:
        return {(row.get("part_id") or "").lower(): row
                for row in csv.DictReader(fh)}


# The catalogue already draws the distinction this needs, in its categories:
#
#   Minifig Accessory   swords, tools, food, cameras, shields — held in a hand
#   Minifig Headwear    hats, hair, helmets — worn on the head
#   Minifig Neckwear    airtanks, backpacks — worn on the torso
#   Minifig             the body itself: head, torso, hips, legs, arms, hands
#
# Which makes the measured table evidence rather than the whole answer. 56
# parts were *seen* held in the reference sets; 294 are accessories. A sword
# released after the sets in the library is still a sword, so the category
# decides whether a part is held and the table decides where — falling back to
# the median grip when a part has never been measured.
ACCESSORY_CATEGORY = "minifig accessory"
HEADWEAR_CATEGORY = "minifig headwear"
NECKWEAR_CATEGORY = "minifig neckwear"

# The median of the 56 measured grips. A default, not a fact.
DEFAULT_GRIP_Y = -9.6


@lru_cache(maxsize=1)
def _accessory_ids():
    """Every part the catalogue files as a thing a minifigure holds."""
    return {(row.get("part_id") or "").strip().lower()
            for row in load_catalog()
            if (row.get("category") or "").strip().lower() == ACCESSORY_CATEGORY}


def held_in_hand(part_id):
    """How a minifigure holds this part, or None if it is not held at all.

    ``grip_y`` is the only number that varies: the hand is a clip and the part
    is a bar pushed through it, so what changes between a sword and a torch is
    how far along the bar the fist closes.
    """
    key = (part_id or "").strip().lower()
    key = key[:-4] if key.endswith(".dat") else key
    row = _grips().get(key)

    if row is None:
        if key not in _accessory_ids():
            return None
        # An accessory the reference sets never happened to hold. It is still
        # held in a hand — that is what the category means — so it gets the
        # rule and the median grip, marked as the guess it is.
        return {
            "grip_y": DEFAULT_GRIP_Y,
            "grip_matrix": None,
            "seen_in_sets": 0,
            "how": ("A minifigure holds this. Place it on the grip axis of the "
                    "hand: the hand's position plus the hand's rotation applied "
                    f"to (0, {DEFAULT_GRIP_Y}, -10.5). That grip is the usual "
                    "one rather than this part's own — no set in the reference "
                    "library holds it, so slide it along the axis until the "
                    "render looks right."),
        }

    try:
        return {
            "grip_y": float(row["grip_y"]),
            "grip_matrix": row.get("grip_matrix") or None,
            "seen_in_sets": int(row["times_held"]),
            "how": ("A minifigure holds this. Place it on the grip axis of the "
                    "hand: the hand's position plus the hand's rotation applied "
                    f"to (0, {float(row['grip_y'])}, -10.5). See the minifigure "
                    "section of your instructions for a worked example."),
        }
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _worn_ids():
    """part_id -> where a minifigure wears it. Built once; this is called for
    every part of every model the validator checks."""
    where = {HEADWEAR_CATEGORY: "head", NECKWEAR_CATEGORY: "torso"}
    return {(row.get("part_id") or "").strip().lower(): worn
            for row in load_catalog()
            if (worn := where.get((row.get("category") or "").strip().lower()))}


def worn_by_minifig(part_id):
    """'head' or 'torso' for a part a minifigure wears, else None."""
    key = (part_id or "").strip().lower()
    key = key[:-4] if key.endswith(".dat") else key
    return _worn_ids().get(key)


@lru_cache(maxsize=1)
def load_catalog():
    if not PARTS_CATALOG.is_file():
        return []
    with open(PARTS_CATALOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(row, key):
    try:
        return float(row.get(key) or "")
    except ValueError:
        return None


def _studs(extent):
    """Convert an LDU extent to a stud count, or None if it is not a clean fit."""
    if extent is None or extent <= 0:
        return None
    n = extent / STUD_PITCH
    return int(round(n)) if abs(n - round(n)) < 0.2 and round(n) >= 1 else None


def stud_offsets(n, centre=0.0):
    """Stud-centre offsets for a footprint n studs wide, about ``centre``.

    ``centre`` is the middle of the part's own bounding box, and passing it is
    the whole point: plenty of parts are not drawn centred on their origin, and
    a 45-degree slope is the common one — 3039 spans z from -30 to +10, so its
    two rows of studs sit at z = -20 and 0, not at the -10 and +10 a part
    centred on nothing would have.

    Assuming the centre was the origin put every one of those out by half a
    stud. That is not a rounding error: it is the exact distance that lands a
    part between two studs instead of on them, and it was being reported to the
    builder as the truth about where the studs are.
    """
    if not n:
        return []
    return [centre - 10.0 * (n - 1) + 20.0 * i for i in range(n)]


def part_geometry(row):
    """Derived grid info for a catalog row."""
    minx, maxx = _num(row, "min_x"), _num(row, "max_x")
    miny, maxy = _num(row, "min_y"), _num(row, "max_y")
    minz, maxz = _num(row, "min_z"), _num(row, "max_z")
    if None in (minx, maxx, miny, maxy, minz, maxz):
        return None

    w = _studs(maxx - minx)
    d = _studs(maxz - minz)
    # Snapped to 10 LDU: a bounding box can run a few tenths wider than the
    # nominal footprint (a curved slope overhangs its own studs), and an
    # unsnapped centre would shift every position off the lattice it is meant
    # to describe.
    snap = lambda v: round(v / 10.0) * 10.0                       # noqa: E731
    xs = stud_offsets(w, snap((minx + maxx) / 2.0)) if w else []
    zs = stud_offsets(d, snap((minz + maxz) / 2.0)) if d else []
    grid = [(x, z) for x in xs for z in zs]

    height = maxy - miny
    if abs(height - 28) < 1.0:
        kind, place_height = "brick", 24.0
    elif abs(height - 12) < 1.0:
        kind, place_height = "plate", 8.0
    else:
        kind, place_height = "other", round(maxy, 3)

    return {
        "bbox": {"x": [minx, maxx], "y": [miny, maxy], "z": [minz, maxz]},
        "width_studs": w,
        "depth_studs": d,
        "kind": kind,
        # subtract this from the y of the part below to stack this part on it
        "place_height_ldu": place_height,
        "stud_grid": grid,
        "has_top_studs": (miny < -1.0),
    }


# --------------------------------------------------------------------------
# What size of thing a part is
#
# A build has three jobs in it and they want three different sizes of part.
# The spine that carries the model wants the longest parts that fit; the body
# that gives it its shape wants ordinary bricks; the details that make it
# readable want 1x1s. That is not a style preference, it is what the corpus
# does — and the failure it names is specific and measured.
#
# Across the 1,797 OMR sets, 98.6% of models use all three of the classes
# below. Across this agent's own 84 models, 54.8% do, and 13% are built out of
# a single class: 10.7% are 90-100% structural (a pile of big bricks) and 52%
# are under 10% structural (a heap of detail with nothing holding it up). The
# agent picks a size and stays there, and both ends of that read as unbuilt.
#
# Pooled over the corpus the mix is:
#
#     structural  15%     medium  42%     detail  43%
#
# Worth reading twice, because it is the opposite of the obvious guess: real
# sets are *detail-heavy*. The structural parts are a spine, not a bulk — about
# one part in seven. Telling a builder "use big pieces" without that number
# produces the 90-100% models above, which are worse than what they replaced.
#
# The thresholds are span-first rather than area-first. A 1x6 brick has the
# same footprint area as a 2x3 and does a completely different job: it *spans*,
# which is what removes a seam, and 20_pieces.md already tells the builder to
# reach for it by name. Measured both ways over the corpus the split barely
# moves (15/42/43 by span against 12/40/48 by area, all-three 98.6% either way),
# so the tie goes to the definition that matches the domain.
STRUCTURAL, MEDIUM, DETAIL = "structural", "medium", "detail"
SIZE_CLASSES = (STRUCTURAL, MEDIUM, DETAIL)

# footprint area in stud^2, and longest side in studs
STRUCTURAL_AREA, STRUCTURAL_SPAN = 8.0, 6.0
MEDIUM_AREA, MEDIUM_SPAN = 2.0, 2.0

SIZE_CLASS_ROLES = {
    STRUCTURAL: "the spine — spans, floors, the parts that carry the model",
    MEDIUM: "the body — the walls and masses that give it its shape",
    DETAIL: "the details — what makes it readable as the thing it is",
}


def size_class(row):
    """Which of the three jobs this part is sized for, or None if unmeasured.

    Takes a catalogue row or anything ``part_geometry`` has already been run
    over — ``get_part`` hands back the latter, and asking for the raw min_x/
    max_x columns on one of those reports every part as unmeasured.
    """
    width = row.get("width_studs")
    depth = row.get("depth_studs")
    if width and depth:
        w, d = float(width), float(depth)
    else:
        minx, maxx = _num(row, "min_x"), _num(row, "max_x")
        minz, maxz = _num(row, "min_z"), _num(row, "max_z")
        if None in (minx, maxx, minz, maxz):
            return None
        w, d = (maxx - minx) / STUD_PITCH, (maxz - minz) / STUD_PITCH
    if w <= 0 or d <= 0:
        return None

    area, span = w * d, max(w, d)
    if area >= STRUCTURAL_AREA or span >= STRUCTURAL_SPAN:
        return STRUCTURAL
    if area >= MEDIUM_AREA or span >= MEDIUM_SPAN:
        return MEDIUM
    return DETAIL


def summarize(row, with_geometry=True):
    out = {
        "part_id": row.get("part_id"),
        "dat_name": row.get("dat_name"),
        "description": row.get("description"),
        "category": row.get("category"),
        "total_uses": row.get("total_uses"),
    }
    # On every search row, not only in the details call: a search is where a
    # part gets chosen, and "a minifigure holds this one" changes how it is
    # placed entirely.
    grip = held_in_hand(row.get("part_id"))
    if grip:
        out["held_in_hand"] = f"held in a minifigure's hand, grip_y {grip['grip_y']}"
    # And for the same reason: a search is where a part gets chosen, and for a
    # slope, a wedge or a bracket, *which way it faces* is half of choosing it.
    # Only on the parts it is a real decision for — see facing_note.
    facing = facing_note(row)
    if facing:
        out["facing"] = facing
    # On every row, and independent of `with_geometry`: which of the three jobs
    # a part is sized for is part of choosing it, not part of placing it. A
    # builder that has already decided it is laying a spine can discard two
    # thirds of a result list on sight.
    sized = size_class(row)
    if sized:
        out["size_class"] = sized
    if with_geometry:
        g = part_geometry(row)
        if g:
            out.update({
                "width_studs": g["width_studs"],
                "depth_studs": g["depth_studs"],
                "kind": g["kind"],
                "place_height_ldu": g["place_height_ldu"],
            })
        # How it joins, on every row of every search — not only in the details
        # call. A search is where a part gets chosen, and choosing one that
        # cannot attach to what it is for is the mistake this prevents; making
        # the agent fetch details for twelve candidates to find that out is how
        # it ends up not checking at all.
        #
        # Counted, not guessed. `part_geometry` decides studs from the bounding
        # box, which is right about three parts in five, and a wrong answer here
        # reaches the agent through every search it runs.
        studs = top_studs(row.get("dat_name") or row.get("part_id"))
        out["top_studs"] = studs
        out["has_top_studs"] = None if studs is None else studs > 0
        joins = part_connections(row)
        out["connections"] = [c["id"] for c in joins["connections"]]
        out["moves"] = joins["moves"]
        out["attaches"] = joins["attachment"]["summary"]
        # A band rather than the raw count: 29,903 uses means nothing on its
        # own, and ranking by it means always choosing the plainest part there
        # is. "uncommon" is the useful signal — a real element, just a
        # specialist one.
        from . import companions

        band = companions.commonness(row.get("set_count"))
        out["commonness"] = band["band"] if band else None
    return out


# --------------------------------------------------------------------------
# Studs on top
#
# Whether you can build on a part is the one thing about it nothing in the CSV
# records, and the bounding box is a poor stand-in: it says "something rises
# above the top face", which is studs on a brick but is also the ramp of a
# slope, the plume on a helmet, and the upper half of a Technic beam whose
# origin sits at its centre. Measured against the real geometry it is wrong
# about two parts in five — so the real geometry is what gets used.
# --------------------------------------------------------------------------

def top_studs(part_name):
    """How many studs a part has on its top face, or None if unreadable.

    None means the library could not answer — a part not on disk, or no library
    at all — and is not the same as zero, which is a tile.
    """
    from . import connections

    scanned = connections.scan(part_name)
    return scanned["top_studs"] if scanned["readable"] else None


# --------------------------------------------------------------------------
# Where a part's studs are, and how to put something on them
#
# The stud grid in the CSV is derived from the bounding box, which is right for
# a rectangle and wrong for everything else — a 2x2 slope has two studs, not
# four, and a bracket's are not where its box says. The real answer is in the
# part file, and the geometry walk in connections.scan already finds it: every
# stud, its position in the part's own coordinates, and which way it points.
#
# What follows turns that into the thing a builder actually needs, which is not
# a list of coordinates but "put a part *here*, turned like *this*".
#
# The turn is the half nobody can work out from a number. A part sits with its
# underside facing +y — down, since -Y is up — and receives a stud pointing -y.
# A stud on the SIDE of a brick points sideways, so a part going onto it has to
# be turned until its underside faces back into that stud. These are the four
# rotations that do it, and they are why a headlight brick or a bracket is
# worth using at all.
# --------------------------------------------------------------------------

_FACING = {
    (1, 0, 0): ("+x", "0 -1 0 1 0 0 0 0 1", "right"),
    (-1, 0, 0): ("-x", "0 1 0 -1 0 0 0 0 1", "left"),
    (0, 0, 1): ("+z", "1 0 0 0 0 1 0 -1 0", "front"),
    (0, 0, -1): ("-z", "1 0 0 0 0 -1 0 1 0", "back"),
}

# The identity, and the three quarter-turns about Y that a SNOT brick is
# actually placed with. Named the way `build_ops` and the technique notes name
# them, so the answer here and the rotation the builder types are the same
# words.
_TURNS = (
    ("none", (1, 0, 0, 0, 1, 0, 0, 0, 1)),
    ("90 about y", (0, 0, 1, 0, 1, 0, -1, 0, 0)),
    ("180 about y", (-1, 0, 0, 0, 1, 0, 0, 0, -1)),
    ("270 about y", (0, 0, -1, 0, 1, 0, 1, 0, 0)),
)


def _mat_mul(a, b):
    """Row-major 3x3 product, ``a`` applied after ``b``."""
    return tuple(
        sum(a[row * 3 + k] * b[k * 3 + col] for k in range(3))
        for row in range(3) for col in range(3))


def _mat_vec(m, v):
    return tuple(m[r * 3] * v[0] + m[r * 3 + 1] * v[1] + m[r * 3 + 2] * v[2]
                 for r in range(3))


def _matrix_text(m):
    def number(value):
        rounded = round(float(value), 4)
        return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"
    return " ".join(number(v) for v in m)


def _parse_matrix(value):
    """Nine numbers from a matrix however it was given, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    else:
        try:
            parts = list(value)
        except TypeError:
            return None
    if len(parts) != 9:
        return None
    try:
        return tuple(float(v) for v in parts)
    except (TypeError, ValueError):
        return None


def _side_studs(part_name):
    """This part's sideways studs, in its own frame. The shared scan.

    Pulled out because `stud_map` and `side_studs_placed` both need it and one
    calling the other recursed without end — `stud_map` now reports the four
    facings, and working those out needs the studs it is in the middle of
    reporting.
    """
    from . import connections

    scanned = connections.scan(part_name)
    if not scanned.get("readable"):
        return []

    out = []
    for point, axis in scanned.get("at") or ():
        if axis[1]:
            continue
        facing, matrix, word = _FACING.get(
            tuple(axis), (str(axis), None, "sideways"))
        out.append({"at": [point[0], point[1], point[2]],
                    "faces": facing, "towards": word,
                    "matrix_for_the_part_on_it": matrix})
    return out


def side_studs_placed(part_name, matrix=None, position=(0.0, 0.0, 0.0),
                      attaching=None):
    """Where a part's side studs really are once the part has been turned.

    This is the answer the builder needs and could not get. ``stud_map`` reports
    a part's side studs in the part's **own** frame — a 1x1 with a stud on one
    side always reports it facing `-z`, because in its own coordinates it always
    does. Turn that brick a quarter turn and the stud faces `-x` in the model
    while the catalogue still says `-z`, and the part built onto it is placed
    against a face that is not there.

    That is the whole of the bug: every SNOT brick placed unrotated connected,
    and every one placed rotated did not, because both were given the same
    answer.

    So the placement is composed in. Returns one entry per side stud:

    * ``at`` — where the stud is **in the model**, not an offset
    * ``faces`` / ``towards`` — the direction it points in the model
    * ``matrix`` — what to put in the type-1 line of the part going onto it,
      which is the host's rotation applied to the facing's own matrix rather
      than either one alone

    ``attaching`` names the part that is going on, and it is what turns this
    from a description into a placement: ``place_at`` is then the exact origin
    for its type-1 line. The offset is the attaching part's own stacking
    height along the facing — a plate stands 8 LDU out from the face, a brick
    24 — which is the same rule as stacking upward, pointed sideways. Measured
    rather than derived: see the sweep in the self-test, where the connecting
    band for a plate is centred on 8 and for a brick on 24, on every SNOT part
    tried.

    Without ``attaching`` there is no ``place_at``, deliberately. The stud
    position on its own is where the *plastic* is, and a part put there is
    inside the host — which is exactly the mistake this function exists to
    stop, so it is not a number worth handing out unqualified.

    ``matrix`` may be given as the nine numbers, as a string, or left out for an
    unrotated part.
    """
    sides = _side_studs(part_name)
    if not sides:
        return []

    host = _parse_matrix(matrix) or (1, 0, 0, 0, 1, 0, 0, 0, 1)
    origin = tuple(float(v) for v in (position or (0.0, 0.0, 0.0)))

    out = []
    for stud in sides:
        local = tuple(float(v) for v in stud["at"])
        # Which way this stud points in the part's own frame, recovered from
        # the facing word rather than carried alongside it.
        axis = next((a for a, (word, _, _) in _FACING.items()
                     if word == stud.get("faces")), None)
        if axis is None:
            continue

        world_axis = _mat_vec(host, axis)
        snapped = tuple(int(round(v)) for v in world_axis)
        facing, own_matrix, word = _FACING.get(
            snapped, (stud.get("faces"), None, "sideways"))

        at = _mat_vec(host, local)
        entry = {
            "at": [round(at[i] + origin[i], 3) for i in range(3)],
            "faces": facing,
            "towards": word,
        }
        base = _parse_matrix(own_matrix)
        if base is not None:
            # The host's rotation applied to the facing's matrix. Taking the
            # facing's matrix alone is what put parts on the wrong face; taking
            # the host's alone leaves the part flat instead of turned onto its
            # side.
            entry["matrix"] = _matrix_text(_mat_mul(host, _parse_matrix(
                _FACING[tuple(axis)][1])))

        stand_off = _stand_off(attaching)
        if stand_off is not None and "matrix" in entry:
            # Out along the face by the attaching part's own stacking height.
            # Its origin at the stud instead would bury it in the host.
            entry["place_at"] = [
                round(entry["at"][i] + snapped[i] * stand_off, 3)
                for i in range(3)]
            entry["stands_off_ldu"] = stand_off
        out.append(entry)
    return out


def _stand_off(part_name):
    """How far out from the face a part seated on a side stud sits.

    Its own stacking height: 8 for a plate, 24 for a brick — the same number
    that decides where it goes when it is stacked upward, because it is the
    same connection turned on its side.
    """
    if not part_name:
        return None
    row = get_part(str(part_name).removesuffix(".dat"))
    height = (row or {}).get("place_height_ldu")
    if not isinstance(height, (int, float)) or height <= 0:
        return None
    return float(height)


def side_stud_facings(part_name):
    """The same, for each quarter turn a SNOT brick is normally placed at.

    For `get_part_details`, so the answer is on the page **before** the builder
    chooses a rotation rather than after it has placed one and found the stud
    pointing somewhere else.
    """
    rows = []
    for name, matrix in _TURNS:
        placed = side_studs_placed(part_name, matrix)
        if not placed:
            continue
        rows.append({
            "rotate": name,
            "matrix_of_this_part": _matrix_text(matrix),
            "studs": [{"offset": s["at"], "faces": s["faces"],
                       "towards": s["towards"],
                       "matrix_for_the_part_on_it": s.get("matrix")}
                      for s in placed],
        })
    return rows


def stud_map(part_name):
    """Every stud on a part: where it is, which way it points, what goes on it.

    ``None`` when the geometry could not be read — no library, or a part that
    is not in it. Positions are in the part's own coordinates, so a part placed
    at ``(x, y, z)`` has a stud at ``(x + sx, y + sy, z + sz)``.
    """
    from . import connections

    scanned = connections.scan(part_name)
    if not scanned.get("readable"):
        return None

    top, seats, sides = [], [], []
    for point, axis in scanned.get("at") or ():
        # -Y is up, so a stud pointing -y is on top and one pointing +y is a
        # tube on the underside: the female half, where this part comes down
        # over somebody else's studs.
        if axis[1] < 0:
            top.append([point[0], point[1], point[2]])
        elif axis[1] > 0:
            seats.append([point[0], point[1], point[2]])
        else:
            facing, matrix, word = _FACING.get(
                tuple(axis), (str(axis), None, "sideways"))
            sides.append({"at": [point[0], point[1], point[2]],
                          "faces": facing, "towards": word,
                          "matrix_for_the_part_on_it": matrix})

    found = {}
    if top:
        found["on_top"] = {
            "count": len(top),
            "at": sorted(top),
            "how": "a part goes on these with no rotation. Its own underside "
                   "positions must land on them: add this part's position to "
                   "the offsets above to get where they are in the model.",
        }
    if seats:
        found["underside"] = {
            "count": len(seats),
            "at": sorted(seats),
            "how": "the tubes underneath — where this part comes down over "
                   "somebody else's studs. These are what have to land on the "
                   "stud positions of the part below, and they are the reason "
                   "a part can be on a multiple of 20 and still not connect.",
        }
    if sides:
        found["on_the_sides"] = {
            "count": len(sides),
            "studs": sides,
            "how": "each of these faces sideways, so the part going onto it is "
                   "turned until its underside faces back into the stud — that "
                   "is what `matrix_for_the_part_on_it` is, ready to paste "
                   "into the type-1 line. This is how a wall gets a surface "
                   "with no studs on it, or a detail that faces the viewer.",
            # The two things that were missing, and between them the whole bug:
            # every number above is in the part's OWN frame, and a SNOT brick is
            # usually placed turned.
            "when_you_turn_this_part": (
                "Everything above is in this part's own coordinates, where the "
                "stud always faces the same way. Turn the part and it does "
                "not: a stud facing `-z` faces `-x` after a 90° turn about Y, "
                "`+z` after 180° and `+x` after 270°, and the matrix for the "
                "part going onto it changes with it. `facings_when_turned` "
                "below has all four worked out — read the one for the rotation "
                "you are placing this at, and never reuse the unrotated matrix "
                "on a turned part."),
            "stand_off": (
                "The part going on does NOT sit at the stud position — that is "
                "where the plastic is, and its origin there would put it "
                "inside this part. It stands off along the facing by its own "
                "stacking height: 8 LDU for a plate, 24 for a brick. Stud at "
                "z = -10 facing -z, adding a plate: the plate's origin is "
                "z = -18."),
            "facings_when_turned": side_stud_facings(part_name),
        }
    return found or None


def part_companions(row, limit=6):
    """Which parts really get used alongside this one, and how common it is."""
    from . import companions

    part_id = row.get("part_id")
    return {
        "used_with": companions.for_part(part_id, limit=limit),
        "commonness": companions.commonness(row.get("set_count")),
    }


def part_connections(row):
    """Which connection families a part belongs to, and what it plugs into."""
    from . import connections

    geometry = part_geometry(row) or {}
    try:
        body = float(row.get("body_height_y") or 0) or None
    except ValueError:
        body = None
    return connections.analyse(
        row.get("dat_name") or row.get("part_id"),
        description=row.get("description"), category=row.get("category"),
        keywords=row.get("keywords"),
        width_studs=geometry.get("width_studs"),
        depth_studs=geometry.get("depth_studs"), body_height=body)


# --------------------------------------------------------------------------
# What the stud grid governs
#
# The connectivity checker understands one connection: a stud in an anti-stud.
# That is most of LEGO and it is nothing like all of it — a Technic pin goes
# into a pin hole, an axle threads a cross-hole, a pane of glass drops into a
# window frame, a bar slides through a clip. None of those parts ever seats on
# a stud, so measuring one against the stud lattice asks a question it has no
# way to answer, and gets a wrong answer rather than no answer: the part is
# judged against a grid it was never on and reported off it.
#
# Measured over the 1,801 official sets, that single mistake was the largest
# source of false alarms in the whole checker — Technic and window glass alone
# accounted for nearly half of every "misaligned" row in models that were
# designed, moulded and sold.
#
# The catalogue already knows. `connections.analyse` reads a part's own
# geometry and name and answers `seats_on_studs`, and this is the one line that
# was never consulted. It is the same move `minifig.py` makes for a figure's
# limbs, generalised to every part held by something the stud checker cannot
# see.
# --------------------------------------------------------------------------

# Round-bodied parts, named rather than measured.
#
# A part whose body is a cylinder seats on a single stud at its centre — see
# `part_has_central_tube` in the connectivity checker, which reads that off the
# part's own wall. Most round parts say so in their geometry; a few draw their
# rim as explicit triangles and measure as square, and 4032a and 60474 are the
# two the reference corpus actually trips over.
#
# So the catalogue answers as well. This is a *relation between named parts and
# a physical fact about them*, which is what a parts catalogue is for — and it
# is safe in a way loosening the geometric test would not be, because it turns
# on the word "Round" rather than on a tolerance that a square 2x2 plate could
# creep through.
_ROUND_BODY_WORDS = ("round", "dish", "cone", "cylinder", "barrel")
# Anything with these in it is round in outline but is not a plain cylinder
# with a tube down the middle: a corner is a quarter, a hole is not a tube.
_NOT_ROUND_BODIED = ("corner", "quarter", "half", "arch", "curved", "slope")


@lru_cache(maxsize=1)
def round_bodied_parts():
    """Names of parts whose body is a cylinder, wider than one stud.

    Returned with and without ``.dat`` so a caller can match either spelling.
    """
    found = set()
    for row in load_catalog():
        described = normalize_text(row.get("description"))
        if not any(w in described for w in _ROUND_BODY_WORDS):
            continue
        if any(w in described for w in _NOT_ROUND_BODIED):
            continue
        geometry = part_geometry(row)
        if not geometry:
            continue
        width, depth = geometry["width_studs"], geometry["depth_studs"]
        # Square, and bigger than the 1x1 the cradle pass already handles.
        if not width or not depth or width != depth or width < 2:
            continue
        part_id = (row.get("part_id") or "").strip().lower()
        if part_id:
            found.add(part_id)
            found.add(f"{part_id}.dat")
    return frozenset(found)


@lru_cache(maxsize=8192)
def seats_on_studs(part_name):
    """Whether the stud grid governs this part at all.

    True for anything that sits on studs — a brick, a plate, a tile, a slope.
    False for a part held by some other system entirely. **None** when the part
    is not in the catalogue, which is not the same answer: an embedded part
    definition inside an MPD is usually an ordinary printed element, and
    guessing "not governed" for it would quietly excuse every part the agent
    invented a number for.
    """
    key = (part_name or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    key = key[:-4] if key.endswith(".dat") else key
    row = _by_id().get(key)
    if row is None:
        return None
    if is_primitive(row):
        # A primitive is the geometry parts are *made of*. Placed directly in a
        # model it is a generated run — a hose path, a rubber band, a rope —
        # and no part of that is seated on anything.
        return False
    try:
        return bool(part_connections(row)["attachment"]["seats_on_studs"])
    except Exception:
        # A part whose connections cannot be read keeps the old behaviour: it
        # is judged, and a false alarm is better than a silent exemption.
        return None


# --------------------------------------------------------------------------
# Which way a part faces
#
# A slope is not a shape, it is a direction, and the same is true of a wedge, a
# bracket, an arch and a printed tile. Turn one and the model looks different;
# turn a 2x4 brick and it is the same 2x4 brick lying the other way.
#
# That distinction is what this is for, and it is why it is *not* read off the
# corpus rotation share. That share says a plain 2x4 brick is turned in 70% of
# its placements, which is true and means nothing — a brick running along z
# carries a 90° matrix, and nobody decided anything by writing it. Flagging
# every part above some threshold would flag four parts in five, which is the
# same as flagging none.
#
# So the question asked here is "does turning this change what it looks like",
# which is a fact about the shape. Category first, because the catalogue
# already sorts most of them; then the words that make an otherwise-square part
# directional — a stud on one side, a clip, a handle, a print.
# --------------------------------------------------------------------------

_FACING_CATEGORIES = {
    "slope", "wedge", "panel", "arch", "bracket", "windscreen",
    "door", "window", "wing", "plant", "animal", "hinge",
}

_FACING_WORDS = (
    "slope", "wedge", "bracket", "arch", "curved", "windscreen", "corner",
    "stud on side", "studs on side", "with clip", "with handle", "with bar",
    "with print", "pattern", "grille", "ramp", "stairs", "fin", "claw",
    "with hook", "with knob", "with door", "with window", "angle",
    # The Erling brick and its family: the recessed stud faces one way, which
    # is the entire reason to use one.
    "headlight",
)

# The three turns worth having, spelled as they go into a type-1 line. About Y
# only: those keep the part flat on the studs and its footprint on the lattice,
# so they can be reached for without re-checking anything underneath.
Y_TURNS = {
    90: "0 0 1 0 1 0 -1 0 0",
    180: "-1 0 0 0 1 0 0 0 -1",
    270: "0 0 -1 0 1 0 1 0 0",
}


def faces_a_direction(row):
    """Whether which way this part is turned changes what it looks like.

    Takes a catalogue row or a part id.
    """
    if not isinstance(row, dict):
        key = (str(row or "").strip().lower().replace("\\", "/")
               .rsplit("/", 1)[-1])
        row = _by_id().get(key[:-4] if key.endswith(".dat") else key) or {}
    if (row.get("category") or "").strip().lower() in _FACING_CATEGORIES:
        return True
    described = normalize_text(row.get("description"))
    return any(word in described for word in _FACING_WORDS)


@lru_cache(maxsize=1)
def _turn_shares():
    """``{part_id: share}`` — how often real sets place each part turned."""
    shares = {}
    try:
        with Path(PART_ROTATION).open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                part_id = (row.get("part_id") or "").strip().lower()
                try:
                    shares[part_id] = float(row.get("share"))
                except (TypeError, ValueError):
                    continue
    except OSError:
        # Mined by build_technique_notes.py. Without it a part simply has no
        # number beside it, which is what it had before this existed.
        pass
    return shares


def turn_share(part_id):
    """How often the corpus places this part turned, 0..1, or None.

    None where the corpus has too few placements to say — see MIN_PLACEMENTS
    in build_technique_notes.py. A share from three placements is not a share.
    """
    key = (str(part_id or "").strip().lower().replace("\\", "/")
           .rsplit("/", 1)[-1])
    return _turn_shares().get(key[:-4] if key.endswith(".dat") else key)


def facing_note(row):
    """One line telling the builder this part has a direction to choose.

    None for a part where it does not — a brick, a plate, a round element —
    because a note on every part is a note on nothing.
    """
    if not faces_a_direction(row):
        return None
    part_id = row.get("part_id") if isinstance(row, dict) else row
    share = turn_share(part_id)
    note = "this part faces a direction — decide which way before you place it"
    if share is not None and share >= 0.5:
        note += f"; real sets turn it in {round(share * 100)}% of placements"
    return note


def normalize_text(text):
    """Lowercase and collapse runs of whitespace.

    Catalogue descriptions pad dimensions for column alignment ("Brick  2 x  4"),
    so a phrase match only works once the spacing is normalised.
    """
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def is_primitive(row):
    """Whether this row is an LDraw primitive rather than a part.

    The library ships the shapes parts are *made of* alongside the parts
    themselves — a cylinder, a box with four faces, the stud itself. They have
    part numbers and bounding boxes like anything else, so nothing but this
    field tells them apart, and offering one as something to build with is
    offering a wall rather than a brick.
    """
    return (row.get("ldraw_org") or "").strip().lower().startswith(
        ("primitive", "subpart"))


def search_parts(query="", category=None, width_studs=None, depth_studs=None,
                 max_results=12):
    rows = [r for r in load_catalog() if not is_primitive(r)]
    query_norm = normalize_text(query)
    terms = [t for t in query_norm.split() if t]

    scored = []
    for row in rows:
        cat = (row.get("category") or "")
        if category and category.lower() not in cat.lower():
            continue
        if cat in ("Moved", "Obsolete"):
            continue

        desc_norm = normalize_text(row.get("description"))
        hay = normalize_text(" ".join((
            row.get("part_id") or "", row.get("description") or "",
            row.get("keywords") or "", cat,
        )))
        # match on whole tokens: a bare "4" must not hit "3004" or "0.667"
        tokens = set(re.findall(r"[a-z0-9.]+", hay))

        if terms and not all(t in tokens for t in terms):
            continue

        g = part_geometry(row)
        if width_studs is not None or depth_studs is not None:
            if not g:
                continue
            w, d = g["width_studs"], g["depth_studs"]
            # accept either orientation, the part can be rotated 90 degrees
            want = {width_studs, depth_studs} - {None}
            have = {w, d} - {None}
            if not want <= have:
                continue

        try:
            uses = int(row.get("total_uses") or 0)
        except ValueError:
            uses = 0

        # exact part number > exact description > description contains the
        # whole query phrase > popularity
        score = uses
        if query_norm and desc_norm == query_norm:
            score += 4_000_000
        elif query_norm and query_norm in desc_norm:
            score += 1_000_000
        if (row.get("part_id") or "").lower() in terms:
            score += 8_000_000
        # unpatterned, unmodified parts are the sane default
        if len(desc_norm) <= len(query_norm) + 12:
            score += 200_000

        scored.append((score, row))

    scored.sort(key=lambda p: -p[0])
    return [summarize(r) for _, r in scored[:max_results]]


# --------------------------------------------------------------------------
# Browsing
#
# The search above answers the agent, which asks one precise question and wants
# a dozen rows back. The one below answers a person scrolling a wall of parts:
# it pages, it counts, it sorts, and it matches on the beginnings of words,
# because somebody typing "brac" has not finished the word yet.
# --------------------------------------------------------------------------

# Categories that describe something other than a piece of plastic you can
# build with. Kept out of the browser unless it is asked for them by name.
RETIRED = ("Moved", "Obsolete")

SORTS = ("relevance", "popular", "name", "id", "size")


def _int(row, key):
    try:
        return int(row.get(key) or 0)
    except ValueError:
        return 0


def normalize_query(text):
    """Normalise, and split "2x4" into the "2 x 4" the descriptions are written in."""
    text = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", " x ", normalize_text(text))
    return text


@lru_cache(maxsize=1)
def _browsable():
    """The catalogue, prepared once for searching: tokens, sizes, popularity."""
    prepared = []
    for row in load_catalog():
        if is_primitive(row):
            continue
        description = row.get("description") or ""
        category = row.get("category") or ""
        desc_norm = normalize_text(description)
        hay = normalize_text(" ".join((row.get("part_id") or "", description,
                                       row.get("keywords") or "", category)))
        geometry = part_geometry(row)
        prepared.append({
            "row": row,
            "part_id": row.get("part_id") or "",
            "desc_norm": desc_norm,
            "tokens": set(re.findall(r"[a-z0-9.]+", hay)),
            "category": category,
            "geometry": geometry,
            "uses": _int(row, "total_uses"),
            "sets": _int(row, "set_count"),
            # a redirect to a replacement part: a signpost, not a part
            "retired": (category in RETIRED or desc_norm.startswith("~")),
        })
    return prepared


@lru_cache(maxsize=1)
def _by_id():
    return {(row.get("part_id") or "").lower(): row for row in load_catalog()}


def summary_for(part_id):
    """The catalogue's own row for a part number, or None."""
    row = _by_id().get((part_id or "").strip().lower())
    return summarize(row) if row else None


def naming(part_id):
    """Just what a part is called, straight off its row.

    Deliberately not `get_part`: that answers with a part's companions, and a
    companion asking what its own companions are called is how you recurse for
    ever. Naming a part needs nothing but the row.
    """
    row = _by_id().get((part_id or "").strip().lower()) or {}
    return {"description": row.get("description"),
            "category": row.get("category")}


def categories():
    """Every category with something in it, and how much, most-stocked first."""
    counts = {}
    for entry in _browsable():
        if entry["retired"]:
            continue
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return [{"name": name, "count": n}
            for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if name]


def _term_matches(term, tokens):
    if term in tokens:
        return True
    return len(term) >= 2 and any(token.startswith(term) for token in tokens)


def browse(query="", category=None, kind=None, width_studs=None,
           depth_studs=None, has_studs=None, connection=None, sort="relevance",
           include_retired=False, limit=48, offset=0):
    """A page of the catalogue, plus how many parts matched in total."""
    query_norm = normalize_query(query)
    terms = [t for t in query_norm.split() if t]
    want_category = (category or "").strip().lower()
    want_kind = (kind or "").strip().lower()
    # One field, three levels of answer: a family ("hinge"), the system it
    # belongs to ("articulated"), or the motion it gives ("spins"). They are
    # asked for in the same place because they are the same question at
    # different resolutions, and a builder has whichever one they have.
    want_connection = (connection or "").strip().lower()

    hits = []
    for entry in _browsable():
        if entry["retired"] and not include_retired:
            continue
        if want_category and entry["category"].lower() != want_category:
            continue

        geometry = entry["geometry"]
        if want_kind:
            if not geometry or geometry["kind"] != want_kind:
                continue
        if width_studs is not None or depth_studs is not None:
            if not geometry:
                continue
            # either way round: a 2x4 is a 4x2 turned ninety degrees
            want = {width_studs, depth_studs} - {None}
            have = {geometry["width_studs"], geometry["depth_studs"]} - {None}
            if not want <= have:
                continue

        if terms and not all(_term_matches(t, entry["tokens"]) for t in terms):
            continue

        # Last, because these are the only tests that have to open a part file.
        # Every cheap filter has already run, so a narrowed search asks the
        # library about the handful of parts that survived rather than all.
        if has_studs is not None:
            count = top_studs(entry["row"].get("dat_name") or entry["part_id"])
            if count is None or (count > 0) != bool(has_studs):
                continue
        if want_connection:
            joins = part_connections(entry["row"])
            if want_connection not in (set(joins["groups"]) | set(joins["moves"])
                                       | {c["id"] for c in joins["connections"]}):
                continue

        score = entry["uses"]
        if query_norm:
            if entry["desc_norm"] == query_norm:
                score += 8_000_000
            elif query_norm in entry["desc_norm"]:
                score += 2_000_000
            if entry["part_id"].lower() in terms:
                score += 16_000_000
            # the plain part before the printed, patterned variant of it
            if len(entry["desc_norm"]) <= len(query_norm) + 12:
                score += 400_000
        hits.append((score, entry))

    area = lambda g: ((g or {}).get("width_studs") or 0) * ((g or {}).get("depth_studs") or 0)
    order = {
        "relevance": lambda p: (-p[0], p[1]["desc_norm"]),
        "popular": lambda p: (-p[1]["uses"], p[1]["desc_norm"]),
        "name": lambda p: (p[1]["desc_norm"], p[1]["part_id"]),
        "id": lambda p: (p[1]["part_id"],),
        "size": lambda p: (-area(p[1]["geometry"]), p[1]["desc_norm"]),
    }
    hits.sort(key=order.get(sort if sort in SORTS else "relevance",
                            order["relevance"]))

    start = max(0, int(offset or 0))
    page = hits[start:start + max(1, int(limit or 48))]
    return {
        "total": len(hits),
        "offset": start,
        "results": [describe(entry["row"]) for _, entry in page],
    }


def describe(row):
    """Everything about a part worth putting on a card."""
    out = summarize(row)
    geometry = part_geometry(row)
    studs = top_studs(row.get("dat_name") or row.get("part_id"))
    joins = part_connections(row)
    # the search row's one-line forms, replaced below by the full objects
    out.pop("attaches", None)
    company = part_companions(row)
    out.update({
        "groups": joins["groups"],
        "moves": joins["moves"],
        "used_with": company["used_with"],
        "commonness": company["commonness"],
        "top_studs": studs,
        "has_top_studs": None if studs is None else studs > 0,
        "connections": joins["connections"],
        "special_connections": joins["special_connections"],
        "attachment": joins["attachment"],
        "keywords": row.get("keywords") or None,
        "set_count": _int(row, "set_count"),
        "total_uses": _int(row, "total_uses"),
        "size_mm": {"width": _num(row, "width_mm"),
                    "height": _num(row, "height_mm"),
                    "depth": _num(row, "depth_mm")},
        "size_ldu": {"width": _num(row, "width_x"),
                     "height": _num(row, "height_y"),
                     "depth": _num(row, "depth_z")},
        "bbox": (geometry or {}).get("bbox"),
        "stud_grid": (geometry or {}).get("stud_grid"),
        "ldraw_org": row.get("ldraw_org") or None,
        "author": row.get("author") or None,
        "source_url": row.get("source_url") or None,
        "dat_name": row.get("dat_name") or None,
    })
    grip = held_in_hand(row.get("part_id"))
    if grip:
        out["held_in_hand"] = grip
    return out


def get_part(part_id):
    key = (part_id or "").strip().lower()
    key = key[:-4] if key.endswith(".dat") else key
    for row in load_catalog():
        if (row.get("part_id") or "").lower() == key:
            info = summarize(row, with_geometry=False)
            g = part_geometry(row)
            if g:
                info.update(g)
            # The whole record, replacing the one-line form summarize puts on a
            # search row. This is the call that gets made once a part has been
            # chosen, so it is where the numbers for placing it belong.
            grip = held_in_hand(row.get("part_id"))
            if grip:
                info["held_in_hand"] = grip
            worn = worn_by_minifig(row.get("part_id"))
            if worn:
                info["worn_by_minifig"] = (
                    f"A minifigure wears this on its {worn}. Place it at the "
                    + ("head's own position — the same coordinates as the head."
                       if worn == "head" else
                       "torso's own position — the same coordinates as the torso."))
            # the counted answer, not the one the bounding box guessed
            studs = top_studs(row.get("dat_name") or row.get("part_id"))
            if studs is not None:
                info["top_studs"] = studs
                info["has_top_studs"] = studs > 0
            # How it joins to anything else, which is the half of a part's
            # description that decides whether a placement is possible at all.
            joins = part_connections(row)
            info["connections"] = [
                {"type": c["id"], "name": c["name"], "roles": c["roles"],
                 "system": c["group"], "does": c["does"]}
                for c in joins["connections"]]
            info["moves"] = joins["moves"]
            # What real sets put beside this part. For half the catalogue this
            # is the fact that turns a lone part into a working assembly — a
            # rim is a hubcap until the tyre goes on it.
            company = part_companions(row, limit=5)
            if company["used_with"]:
                info["used_with"] = [
                    {"part_id": c["part_id"], "description": c["description"],
                     "in_sets_pct": c["in_sets_pct"]}
                    for c in company["used_with"]]
                info["used_with_note"] = (
                    "Parts that real sets put alongside this one, with how "
                    "often. A high percentage is a part you probably need too: "
                    "93% means nine times in ten this part was not used alone.")
            if company["commonness"]:
                info["commonness"] = company["commonness"]["band"]
            info["attachment"] = joins["attachment"]["summary"]
            info["studs_required"] = joins["attachment"]["studs_required"]
            info["keywords"] = row.get("keywords")
            return info
    return None
