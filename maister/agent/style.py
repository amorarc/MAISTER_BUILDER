"""How varied a build is, measured against the sets that came in boxes.

Every other check in this project asks whether a model can be built. None of
them asks whether it is worth building. A tower of ninety identical 2x2 bricks
passes validation perfectly: every part on the grid, nothing overlapping, no
part invented. It is also not a model of anything.

That gap is measurable, and it does not need a vision model to measure it. Four
numbers separate what this agent builds from what LEGO ships:

* **how many distinct shapes** are in it,
* **what share the commonest shape takes** — the one that catches a build made
  out of a single brick,
* **how many colours**,
* **what share of parts carry a rotation** — the one that catches a build that
  is all right angles.

``BASELINES`` below is those four numbers over the 1,812 models in the OMR
corpus, bucketed by piece count because a 20-part build and a 2,000-part build
are not comparable on any of them. So the report can say something specific and
true — *"3003 is 79% of this model; in real sets its size the commonest part is
9%"* — which is a fact the builder can act on, and not an opinion it can argue
with.

# This never fails a model

It returns observations, never faults. ``validate_model`` keeps meaning "can
this be built", and nothing here changes its verdict.

That is deliberate, and it is the same reasoning that picked the vision critic
in config.py: a check that invents problems is worse than no check, because the
builder goes and "fixes" them. Monotony is sometimes correct — a brick wall is
made of bricks, a 12-part footbridge has nothing to be varied about — so the
thresholds are set at the **tenth percentile** of the real corpus. A model only
gets a remark when it is duller than nine out of ten real sets its own size,
and a remark is an invitation, not an error.
"""

# Measured over every .mpd and .ldr in data/ldraw_omr_sets — 1,812 models —
# by tools/style_baselines.py. Regenerate it with that script if the corpus
# changes; do not hand-edit these numbers.
#
# Read at the source-line level: type-1 lines that name a real part, with
# submodel references excluded. A part sitting inside a submodel that is itself
# placed rotated therefore counts as unrotated. That undercounts rotation — but
# it undercounts it identically for the corpus and for the model being checked,
# which is what makes the comparison honest, and it answers the question that
# actually matters: did whoever wrote this file ever write a rotation.
BASELINES = {
    # (min_parts, max_parts): medians, with the percentile that gates a remark
    (1, 25): {
        "distinct": 11, "top_share": 0.182, "top_share_p90": 0.320,
        "colours": 5, "colours_p10": 3,
        "rotated_share": 0.696, "rotated_share_p10": 0.471,
    },
    (26, 60): {
        "distinct": 23, "top_share": 0.121, "top_share_p90": 0.184,
        "colours": 7, "colours_p10": 5,
        "rotated_share": 0.702, "rotated_share_p10": 0.500,
    },
    (61, 150): {
        "distinct": 39, "top_share": 0.098, "top_share_p90": 0.175,
        "colours": 9, "colours_p10": 5,
        "rotated_share": 0.728, "rotated_share_p10": 0.544,
    },
    (151, 400): {
        "distinct": 64, "top_share": 0.093, "top_share_p90": 0.209,
        "colours": 11, "colours_p10": 6,
        "rotated_share": 0.737, "rotated_share_p10": 0.561,
    },
    (401, 1200): {
        "distinct": 112, "top_share": 0.097, "top_share_p90": 0.283,
        "colours": 14, "colours_p10": 8,
        "rotated_share": 0.767, "rotated_share_p10": 0.607,
    },
    (1201, 10 ** 9): {
        "distinct": 165, "top_share": 0.155, "top_share_p90": 0.812,
        "colours": 17, "colours_p10": 10,
        "rotated_share": 0.835, "rotated_share_p10": 0.551,
    },
}

# Below this the numbers say nothing. A nine-part build made of one brick is a
# nine-part build made of one brick, and there is no version of it that scores
# well — remarking on it would be noise on exactly the builds that are already
# finished and correct.
MIN_PARTS = 12

# --------------------------------------------------------------------------
# The size mix
#
# The fifth axis, and the one this agent is furthest off. Every part is one of
# three sizes — see catalog.size_class — and the three do three different jobs:
# a spine that spans, a body that gives the model its shape, details that make
# it readable.
#
# Measured over the same 1,797 OMR models the numbers above came from:
#
#     structural  15%     medium  42%     detail  43%      all three: 98.6%
#
# and over this agent's own 84 models:
#
#     structural  21%     medium  35%     detail  44%      all three: 54.8%
#
# The pooled shares are close enough to look fine, and they hide the whole
# fault. The agent's *per-model* distribution is bimodal: 10.7% of its models
# are 90-100% structural and 52% are under 10%, against a corpus where 97% of
# sets sit between 0 and 30%. It is not building the wrong mix, it is not
# mixing — it picks one size of part and builds the entire object out of it.
#
# So the remark is about coverage first and proportion second. A model missing
# a whole class is the case worth speaking on; a model with all three in
# unusual proportions is usually just a model.
SIZE_CLASS_SHARE = {"structural": 0.15, "medium": 0.42, "detail": 0.43}
# 97% of real sets are inside this band. Past it in either direction is a pile
# of big bricks or a heap of loose detail.
STRUCTURAL_BAND = (0.02, 0.35)
# Below this a class counts as absent rather than merely thin: one 1x1 in a
# 90-part model is not a class the build is using.
CLASS_PRESENT = 0.03

# Two gates, and a remark needs both.
#
# The percentile alone is not enough, and the corpus proves it: a tenth
# percentile flags a tenth of real sets by construction, so on the first pass
# set 40440 — designed by LEGO, built out of real bricks, sold in a box — was
# told its rotation was low. A check that corrects professional work is the
# failure mode config.py already names for the vision critic, and it costs more
# than it returns: the builder spends steps "fixing" a model that was right.
#
# So a remark also needs the gap to be *large*, measured against the median
# rather than the tail. Past the percentile says unusual; past the margin says
# unusual enough that the advice is obviously worth taking.
#
# These three were tuned against the corpus itself. At these values 1.6% of
# real sets between 12 and 60 parts get a remark, and 5.5% of those between 61
# and 400 — the range this agent builds in — while every model it has produced
# out of a single repeated brick is still caught.
#
# Above 400 parts the rate stays near 11%, and that is left alone: the sets
# flagged there are mosaics and Technic, which genuinely are one part repeated
# four thousand times. Tuning them out would cost the sensitivity that catches
# a 76-part tree made of 62 identical round plates, and this agent does not
# build 10,000-part mosaics.
TOP_SHARE_MARGIN = 3.0     # commonest part at three times the typical share
ROTATION_MARGIN = 0.35     # rotation at barely a third of typical
COLOUR_MARGIN = 0.4        # colours at under half the typical count

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def baseline(parts):
    """What real sets of this piece count look like, or None if out of range."""
    for (low, high), row in BASELINES.items():
        if low <= parts <= high:
            return row
    return None


# Kept as the old private name: this module used it before the planner needed
# it too, and there is no reason for two spellings of one lookup.
_baseline = baseline


def _bare(name):
    """`parts/3001.dat` -> `3001`, the form the catalogue is keyed by."""
    bare = (name or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    return bare[:-4] if bare.endswith(".dat") else bare


def measure(instances, known=None):
    """The four numbers, from parsed type-1 lines.

    ``instances`` is anything with ``.part_name``, ``.color`` and ``.matrix`` —
    which is what the collision checker's ``PartInstance`` already is, so this
    runs off the parse ``validate`` has already paid for.

    ``known`` is the set of real part ids, without ``.dat``. When it is given,
    anything else is skipped as an LDraw primitive — the internal geometry that
    part definitions are built from, which appears as type-1 lines exactly like
    a part does and is nothing anybody chose. It matters because the corpus the
    baselines came from is filtered the same way; measuring the two differently
    would compare a model against a distribution built by other rules.
    """
    from . import catalog

    sizes = {}
    if known:
        # part_id -> size_class, built once off the catalogue rows. `known` is
        # only a set of ids, and the class needs the row's bounding box.
        for row in catalog.load_catalog():
            pid = (row.get("part_id") or "").strip().lower()
            if pid:
                sizes[pid] = catalog.size_class(row)

    shapes, colours, rotated = {}, set(), 0
    by_size = {}

    for inst in instances:
        name = (getattr(inst, "part_name", "") or "").strip().lower()
        if not name:
            continue
        if known and _bare(name) not in known:
            continue
        shapes[name] = shapes.get(name, 0) + 1
        sized = sizes.get(_bare(name))
        if sized:
            by_size[sized] = by_size.get(sized, 0) + 1
        colour = str(getattr(inst, "color", "") or "").strip()
        if colour:
            colours.add(colour)
        matrix = getattr(inst, "matrix", None) or []
        try:
            values = tuple(round(float(v), 6) for v in matrix)
        except (TypeError, ValueError):
            values = ()
        if len(values) == 9 and values != IDENTITY:
            rotated += 1

    total = sum(shapes.values())
    if not total:
        return None

    commonest = max(shapes.items(), key=lambda kv: kv[1])
    measured_sizes = sum(by_size.values())
    return {
        "parts": total,
        "size_counts": dict(by_size),
        "size_shares": ({k: v / measured_sizes for k, v in by_size.items()}
                        if measured_sizes else {}),
        "sized_parts": measured_sizes,
        "distinct_shapes": len(shapes),
        "commonest_part": commonest[0],
        "commonest_count": commonest[1],
        "top_share": commonest[1] / total,
        "colours": len(colours),
        "rotated": rotated,
        "rotated_share": rotated / total,
    }


def _pct(value):
    return f"{round(value * 100)}%"


def report(instances, known=None):
    """Measurements plus, when the build is a genuine outlier, what to do.

    Returns None when there is nothing worth saying — too few parts to judge,
    or a build already within the range real sets occupy. A key that is not
    there costs the builder no tokens and no attention, which is the point:
    this speaks on the builds where it has something to add and is silent on
    the rest.
    """
    found = measure(instances, known)
    if not found or found["parts"] < MIN_PARTS:
        return None

    base = _baseline(found["parts"])
    if not base:
        return None

    remarks = []

    # One shape doing all the work. This is the single strongest signal that a
    # build was assembled rather than designed, and it is the one this agent
    # trips most: 141 of 178 parts, one brick.
    if (found["top_share"] > base["top_share_p90"]
            and found["top_share"] > base["top_share"] * TOP_SHARE_MARGIN):
        remarks.append(
            f"`{found['commonest_part']}` is {_pct(found['top_share'])} of this "
            f"model ({found['commonest_count']} of {found['parts']} parts). In "
            f"real sets this size the commonest part is about "
            f"{_pct(base['top_share'])}. Whatever this build is approximating "
            f"by repeating one brick — a curve, a texture, a slope, foliage — "
            f"there is a part for it: search for the shape instead of stacking "
            f"toward it.")

    # Everything at right angles. Real sets rotate roughly seven parts in ten,
    # at every size, which is the most stable number in the whole corpus.
    if (found["rotated_share"] < base["rotated_share_p10"]
            and found["rotated_share"] < base["rotated_share"] * ROTATION_MARGIN):
        remarks.append(
            f"{_pct(found['rotated_share'])} of the parts carry a rotation; in "
            f"real sets this size it is about {_pct(base['rotated_share'])}. "
            f"Slopes facing four ways, a wall turned with brackets, wedges set "
            f"at an angle — rotation is most of what stops a build reading as "
            f"a stack of boxes.")

    # The size mix. Coverage first: a model missing a whole class is the case
    # worth speaking on, and it is the one this agent trips — 13% of its models
    # are built out of a single size of part.
    shares = found.get("size_shares") or {}
    if found.get("sized_parts", 0) >= MIN_PARTS:
        missing = [name for name in ("structural", "medium", "detail")
                   if shares.get(name, 0.0) < CLASS_PRESENT]
        if missing:
            have = ", ".join(
                f"{name} {_pct(shares.get(name, 0.0))}"
                for name in ("structural", "medium", "detail"))
            wanted = {
                "structural": ("nothing in this model spans — no part 6 studs "
                               "long or 8 studs of footprint. A spine is what "
                               "stops a build coming apart when it is picked "
                               "up, and it is about one part in seven"),
                "medium": ("there is a spine and there is decoration, and "
                           "nothing in between — the walls and masses that "
                           "give a model its shape are 2x2 to 2x4 bricks, and "
                           "they are the largest share of a real set"),
                "detail": ("nothing here is 1x1 — no tiles, no cheese slopes, "
                           "no round bricks. Detail is what makes a shape read "
                           "as the thing it is, and it is 43% of a real set"),
            }
            remarks.append(
                f"This model is built out of "
                f"{3 - len(missing)} of the 3 sizes of part ({have}); 98.6% of "
                f"real sets use all three. Missing: "
                + "; ".join(f"**{name}** — {wanted[name]}" for name in missing)
                + ". Real sets run about 15% structural, 42% medium, 43% "
                  "detail.")
        else:
            spine = shares.get("structural", 0.0)
            low, high = STRUCTURAL_BAND
            if spine > high:
                remarks.append(
                    f"{_pct(spine)} of this model is structural parts — parts "
                    f"6+ studs long or 8+ studs of footprint. In real sets it "
                    f"is about {_pct(SIZE_CLASS_SHARE['structural'])}, and 97% "
                    f"sit under {_pct(high)}. Big parts are the spine, not the "
                    f"body: the shape comes from ordinary bricks laid over "
                    f"them and the detail that finishes them.")
            elif spine < low:
                remarks.append(
                    f"Only {_pct(spine)} of this model spans anything. A build "
                    f"with no structural parts in it is held together by the "
                    f"studs of small parts alone, which is what makes a model "
                    f"come apart when it is lifted — and every seam between "
                    f"two short parts is a seam that shows.")

    if (found["colours"] < base["colours_p10"]
            and found["colours"] <= base["colours"] * COLOUR_MARGIN):
        remarks.append(
            f"{found['colours']} colour(s), against about {base['colours']} in "
            f"real sets this size. An accent colour on edges, frames, or the "
            f"details that catch the eye is usually what is missing.")

    if not remarks:
        return None

    return {
        "measured": {
            "parts": found["parts"],
            "distinct_shapes": found["distinct_shapes"],
            "commonest_part": found["commonest_part"],
            "top_share": round(found["top_share"], 3),
            "colours": found["colours"],
            "rotated_share": round(found["rotated_share"], 3),
            "size_mix": {k: round(v, 3) for k, v in
                         (found.get("size_shares") or {}).items()},
        },
        "typical_for_this_size": {
            "distinct_shapes": base["distinct"],
            "top_share": base["top_share"],
            "colours": base["colours"],
            "rotated_share": base["rotated_share"],
            "size_mix": SIZE_CLASS_SHARE,
        },
        "observations": remarks,
        "note": ("This is not a fault and it does not fail the model — it is a "
                 "comparison against the 1,812 official sets in the reference "
                 "corpus, on the axes that separate a designed model from an "
                 "assembled one. Act on it if the build has steps left in it, "
                 "and ignore it where repetition is the right answer: a brick "
                 "wall is made of bricks."),
    }
