"""Which parts are used *with* which — learned from real sets.

A part's dimensions say what it is. What they never say is what it is normally
used *with*, and for a great many parts that is the more useful fact. A wheel
rim is not a thing you place; it is half of a pair, and without the tyre that
stretches over it you have modelled a hubcap. A Technic pin belongs with a
beam. A clip belongs with a bar. A turntable comes in two halves that are
separate part numbers and are useless apart.

None of that is derivable from the geometry. It is derivable from 1,463 real
LEGO sets, in ``part_set_usage.csv``: two parts that keep turning up in the
same box are two parts that go together.

Two numbers are needed, and either one alone gets it wrong.

**Lift** — how much more often the pair appears together than it would if sets
were assembled at random, ``P(companion | this) / P(companion)``. This is what
separates a habit from a coincidence: a 1x2 plate sits at a lift near 1 against
everything in the catalogue, because it is in almost every box and so is
"companion" to nothing. Ranking by lift alone, though, hands the top spot to
whatever rare part happened to share a few boxes — the rarer it is, the higher
it scores.

**Share** — of the sets that use this part, how many also use that one. This is
the number a builder can act on, and ranking by it alone is just as wrong: it
answers every question with the plate that is in every set.

So a pair has to clear a lift floor to be believed at all, and what survives is
ordered by share. For a turntable top, that puts its own base first, in 93% of
the sets it appears in, at a lift of 15 — while the ubiquitous plates it also
sits beside never clear the floor. A part with nothing above both thresholds
reports no companions, which for a 2x4 brick is the truthful answer: it is used
with everything, so it is characteristic of nothing.

The table is built once and cached beside the catalogue: 7.6 million pair
counts is a few seconds of work and not something to repeat per question.
"""

import csv
import math
from collections import defaultdict
from functools import lru_cache

from .config import PARTS_CATALOG

COMPANIONS_FILE = PARTS_CATALOG.parent / "part_companions.csv"

# How many companions are kept per part. Enough to show a wheel its rim, its
# tyre and the axle that carries it; not so many that the tail of coincidences
# gets in.
TOP_N = 12

# Below this many sets in common, a pair is a coincidence rather than a habit.
MIN_TOGETHER = 4

# A part in fewer sets than this has no reliable companions at all — every one
# of them would be drawn from a handful of boxes.
MIN_SETS = 3

# How much likelier than chance a pairing has to be before it is believed. Two
# parts that merely both turn up a lot sit near 1.
MIN_LIFT = 4.0

# And how much of the time it has to actually happen. Below this the pairing is
# real but not worth telling a builder about: it is a thing that sometimes goes
# with this part, not a thing that goes with it.
MIN_SHARE = 0.15


def build(verbose=False):
    """Compute the companion table and write it beside the catalogue."""
    from . import catalog, sets

    usage = sets.load_usage()
    if not usage:
        return 0

    # A "~Moved to 4449-f1" row is a signpost to a replacement, not a part
    # anybody can place. Offering one as a companion sends the agent after a
    # number that resolves to nothing.
    real = {(row.get("part_id") or "").lower()
            for row in catalog.load_catalog()
            if not (row.get("description") or "").startswith("~")
            and (row.get("category") or "") not in ("Moved", "Obsolete")
            and not catalog.is_primitive(row)}

    in_sets = defaultdict(int)              # part -> how many sets hold it
    together = defaultdict(int)             # (a, b) ordered pair -> sets in common
    for parts in usage.values():
        ids = sorted({p for p, _q in parts if p and p.lower() in real})
        for part in ids:
            in_sets[part] += 1
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                together[(a, b)] += 1

    total = len(usage)
    best = defaultdict(list)
    for (a, b), n in together.items():
        if n < MIN_TOGETHER:
            continue
        for this, other in ((a, b), (b, a)):
            if in_sets[this] < MIN_SETS:
                continue
            # lift: how much likelier `other` is in a set holding `this` than
            # in a set picked at random
            expected = in_sets[other] / total
            if expected <= 0:
                continue
            share = n / in_sets[this]
            lift = share / expected
            if lift < MIN_LIFT or share < MIN_SHARE:
                continue
            best[this].append((lift, n, share, other))

    COMPANIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(COMPANIONS_FILE, "w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["part_id", "companion_id", "sets_together", "share", "lift"])
        for part, rows in best.items():
            # by share: everything here has already earned its place on lift,
            # so what orders it is how often you will actually want it
            rows.sort(key=lambda r: (-r[2], -r[0]))
            for lift, n, share, other in rows[:TOP_N]:
                out.writerow([part, other, n, f"{share:.4f}", f"{lift:.2f}"])
                written += 1
    if verbose:
        print(f"{written} companion rows for {len(best)} parts -> {COMPANIONS_FILE}")
    return written


@lru_cache(maxsize=1)
def _table():
    """``{part_id: [companion, ...]}``, built on first use if not on disk."""
    if not COMPANIONS_FILE.is_file():
        try:
            build()
        except Exception:
            return {}
    if not COMPANIONS_FILE.is_file():
        return {}

    table = defaultdict(list)
    try:
        with open(COMPANIONS_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    table[(row["part_id"] or "").lower()].append({
                        "part_id": row["companion_id"],
                        "sets_together": int(row["sets_together"]),
                        "share": float(row["share"]),
                        "lift": float(row["lift"]),
                    })
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {}
    return dict(table)


def for_part(part_id, limit=6, with_descriptions=True):
    """The parts most characteristically used alongside this one."""
    from . import catalog

    key = (part_id or "").strip().lower()
    key = key[:-4] if key.endswith(".dat") else key
    rows = _table().get(key) or []

    out = []
    for row in rows[:limit]:
        entry = dict(row)
        if with_descriptions:
            entry.update(catalog.naming(row["part_id"]))
        # "in 78% of the sets that use this part" is the number a person can
        # act on; lift is what ranked it and is kept for anyone who wants it
        entry["in_sets_pct"] = round(row["share"] * 100)
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# How common a part is
#
# `total_uses` is a number in the tens of thousands and tells nobody anything
# on its own. What a builder wants to know is whether reaching for this part is
# ordinary or exotic — a 1x2 plate is in a third of all sets, a chrome minifig
# trident is in two.
# --------------------------------------------------------------------------

BANDS = (
    (0.10, "very common", "in more than a tenth of all sets"),
    (0.02, "common", "in a few sets in every hundred"),
    (0.004, "uncommon", "a specialist part, but a real one"),
    (0.0, "rare", "almost never used — check there is not a plainer part "
                  "that does the same job"),
)


@lru_cache(maxsize=1)
def _set_total():
    from . import sets

    return len(sets.load_usage()) or 1


def commonness(set_count):
    """What a part's set count means, in words."""
    try:
        count = int(set_count or 0)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    share = count / _set_total()
    for floor, name, blurb in BANDS:
        if share >= floor:
            return {"band": name, "sets": count,
                    "share_pct": round(share * 100, 1), "note": blurb}
    return None


def _log(x):  # kept for callers that want the raw scale
    return math.log(max(x, 1e-9))
