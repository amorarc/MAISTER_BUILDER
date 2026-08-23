"""How big the thing is, when nobody said.

Every build in this project has a size, and until now nothing decided it. The
decomposer was asked for a ``size_hint`` and invented one per object, freely,
with no default and nothing to anchor it against — so "build this bonsai!"
came back as *"about 20 x 20 studs footprint, 15 bricks tall"*, which is a
250-piece model, and the run spent eleven minutes on it and ended with the
thing in seven pieces on the workbench.

That is the failure this module exists for, and it is worth being exact about
what went wrong, because it was not the size *estimate*. Nothing was
unreasonable about 20 x 20 for a bonsai. It is that a size was chosen at all by
a pass whose job is counting objects, on a request that never mentioned one,
and every pass after it then treated the invention as a specification: the
brief wrote to it, the planner budgeted to it, the requirements gate refused
the model for not meeting it.

# The rule

**A size that was asked for is obeyed. A size that was not asked for is small.**

Small is the default and it is not a hedge. A small model finishes, and a
finished small model is worth more than an unfinished large one — which is the
same argument the orchestrator already makes for building objects one at a
time, and the same one the trace makes for rendering every write. A build that
comes out too small is one more request away from being right. A build that
runs out of steps at 250 pieces is nothing at all.

# The bands

Measured over the 1,797 OMR sets, by piece count — the same buckets
``style.BASELINES`` uses, since they were cut for the same reason:

    pieces        median longest side    median height
    1-25           7 studs                3 bricks
    26-60          9 studs                4 bricks
    61-150        15 studs                7 bricks
    151-400       25 studs               10 bricks
    401-1200      38 studs               16 bricks

So the three bands below are not invented proportions. They are what a real set
of that piece count actually measures, which means a build told to aim at one
is being told to look like the sets it is being compared against on every other
axis.
"""

import re

# name, pieces, longest side in studs, height in bricks
BANDS = {
    "tiny": (18, 7, 3),
    "small": (45, 9, 4),
    "medium": (110, 15, 7),
    "large": (260, 25, 10),
}
DEFAULT = "small"

# Words that are a user asking for a size, in so many words. Deliberately
# explicit, for the reason `brief._INVITES` is: a parser that read every
# adjective as a size would put the invention back where it was, only with
# worse judgement. "A red car" says nothing about size and gets the default.
_WORDS = (
    ("tiny", r"tiny|micro|miniature|thumbnail|keychain|tiniest"),
    ("small", r"small|little|compact|modest|simple|quick|smaller"),
    ("medium", r"medium|mid-sized|middling|moderate|average"),
    ("large", r"large|big|huge|giant|enormous|massive|grand|"
              r"full-size|full size|bigger|largest"),
)
# Deliberately absent from the list above: "detailed", "elaborate",
# "impressive", "ornate". They read as size words and are not — they are about
# how much is *on* a model, not how big it is, and `brief._INVITES` already
# takes them as licence to invent. Left in, they beat an explicit "small": "a
# small but detailed house" came back as a 260-piece build, which is the
# opposite of both halves of what was asked.
_PATTERNS = [(name, re.compile(rf"\b({words})\b", re.I)) for name, words in _WORDS]

# "20 studs wide", "about 12 studs", "a 32 x 32 baseplate"
_STUDS = re.compile(r"\b(\d{1,3})\s*(?:x\s*\d{1,3}\s*)?studs?\b", re.I)
_GRID = re.compile(r"\b(\d{1,3})\s*x\s*(\d{1,3})\b", re.I)
# "under 50 pieces", "about 200 parts"
_PIECES = re.compile(r"\b(\d{1,4})\s*(?:pieces?|parts?|bricks?)\b", re.I)


def _band_for_pieces(pieces):
    """The band whose piece count is nearest ``pieces``."""
    return min(BANDS, key=lambda name: abs(BANDS[name][0] - pieces))


def _band_for_studs(studs):
    return min(BANDS, key=lambda name: abs(BANDS[name][1] - studs))


def requested(*texts):
    """``(band, why, exact)`` for whatever the request said about size.

    ``why`` is ``"asked"`` when the request stated one and ``"default"`` when
    it did not — which is the distinction every caller actually needs, because
    a stated size is a requirement and a defaulted one is only a starting
    point. See ``requirements.py``: a gate that refuses a model for missing a
    size nobody asked for is a gate refusing its own invention.
    """
    text = " ".join(str(t or "") for t in texts)
    if not text.strip():
        return DEFAULT, "default", None

    # A number beats a word: "a small 40-piece house" is 40 pieces. The number
    # itself is carried out as `exact`, because snapping "under 200 pieces" to
    # the nearest band budgets 260 — which is not what was asked and is the
    # one direction a budget must not move.
    pieces = _PIECES.search(text)
    if pieces:
        count = int(pieces.group(1))
        return _band_for_pieces(count), "asked", {"pieces": count}
    studs = _STUDS.search(text) or _GRID.search(text)
    if studs:
        across = int(studs.group(1))
        return _band_for_studs(across), "asked", {"studs": across}

    # Largest wins where two size words appear — a genuine conflict, and the
    # bigger reading is the one that cannot be satisfied by accident.
    found = [name for name, pattern in _PATTERNS if pattern.search(text)]
    if found:
        order = list(BANDS)
        return max(found, key=order.index), "asked", None
    return DEFAULT, "default", None


def hint(band):
    """The ``size_hint`` sentence for a band."""
    _, studs, bricks = BANDS[band]
    # No leading "about": the callers introduce it ("Aim for roughly ..."),
    # and two hedges in one sentence read as a size nobody meant.
    return f"{studs} x {studs} studs and {bricks} bricks tall, a {band} build"


def max_pieces(band):
    """The piece budget for a band, for ``plan_construction``."""
    return BANDS[band][0]


def resolve(petition, requirements=None, stated=None):
    """What size to build at, as ``(band, size_hint, max_pieces, why)``.

    ``stated`` is whatever the decomposer put in ``size_hint``. It is kept when
    the request actually asked for a size and dropped when it did not — the
    decomposer's job is counting objects, and a size it invented on a request
    that never mentioned one is not evidence of anything.
    """
    band, why, exact = requested(petition, requirements)
    budget = max_pieces(band)
    if exact and "pieces" in exact:
        budget = exact["pieces"]
    if why == "asked" and stated:
        return band, str(stated), budget, why
    if exact and "studs" in exact:
        _, _, bricks = BANDS[band]
        across = exact["studs"]
        return (band, f"{across} x {across} studs and {bricks} bricks tall",
                budget, why)
    return band, hint(band), budget, why
