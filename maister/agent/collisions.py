"""Which parts are actually inside each other, and what to do about it.

The collision checker reports every pair of parts whose boxes overlap. Most of
those are not defects: LEGO connects *by* overlapping. A stud stands 4 LDU
proud and sits that far inside the part above it, so every correctly stacked
brick in a model overlaps the one below. That is why the raw count was only
ever reported as a number to be ignored - and why a brick genuinely buried
inside another one went unmentioned along with it.

What separates the two is how deep the overlap goes. Push two parts together
and the smallest distance that would pull them apart again - the shallowest of
the three axes - is the depth of engagement:

    a plate resting on a brick     4 LDU   (the studs, and nothing more)
    a brick one stud too far east  20 LDU  (a whole stud of solid plastic)
    a brick placed twice           its own full width

So an overlap deeper than a stud's engagement, on every axis, is two parts
occupying the same space. That is a defect, it is reported as one, and it comes
with the move that would fix it.

Deep engagements that are real do exist - an axle through a wheel, a bar in a
clip, a neck in a torso - but every one of them is a part that does not fill its
own box, and those are excluded before any of this is measured (see
``_solid_shapes``). What is left are plain rectangular bricks and plates, and
for two of those, depth is the whole story: 10 LDU of shared plastic between two
boxes is 10 LDU of shared plastic, and no further test is needed to know it
cannot be built.

# The above is now the FALLBACK. Read occupancy.py first.

Everything described here is a box, plus a list of words guessing whether the
box resembles the part. Both halves fail on the parts a good model is made of,
and the cost was measured rather than suspected: only 141 of 5,878 catalogue
parts could ever be judged, and across the project models on disk **633 of 797
overlaps deeper than a contact were never looked at** - two 2x2 slopes sharing
a full stud, a 2x4 brick buried inside a double slope, a bracket driven through
a brick, every one of them answered "nothing overlaps".

So the decision is made by measuring the plastic instead - see ``occupancy``,
which rasterises each part's real mesh once and counts the voxels two
placements share. It needs no exemptions at all, because correctly assembled
LEGO parts share no material: a stud goes *into* a tube, a bracket holds a
plate *beside* its upstand, a dish *nests* in a dish.

What survives here, and why it is not deleted:

* the **sweep-and-prune AABB pass**, which is the broad phase. A box test is
  what makes it affordable to measure only the pairs that could possibly touch.
* ``_describe``, which turns an overlap into the move that resolves it. That is
  arithmetic on boxes and is as good as it ever was.
* this whole reading as the **fallback**, for a caller with no library to read
  geometry out of - see ``classify_pair``'s ``measure`` argument.
* **how much shared plastic is too much**, which is a policy rather than a
  measurement and so lives here rather than in ``occupancy`` - see
  ``SHARED_FRACTION``, which is what makes the check as sensitive on a slope
  or a 1x1 as it always was on a 2x4.

There used to be one. A pair also had to share 18% of the smaller part's volume,
and that hid a whole class of real defect, because the fraction is not a
property of the overlap - it is a property of how *long* the other part happens
to be. A tank's track plate laid a stud too far inboard shares its full length
with the hull course beside it, 10 LDU deep along the seam, and comes to 11% of
a 2x12 plate: invisible. The same seam against a 2x2 brick would have been 60%
and reported at once. Two placements, identically wrong, and the checker's
answer depended on which was the bigger piece. Measured across seventeen
official sets, dropping the test changed nothing in fifteen of them and found
sixteen more full-stud interpenetrations in one already known to be badly
modelled - so what it was holding back was not false alarms.
"""

import math

from . import catalog, occupancy

# How far a stud stands into the part above it. The engagement every correct
# stack has, and the line between "connected" and "inside each other".
STUD_ENGAGEMENT = 4.0

# Plus room for the parts whose sockets swallow a little more than a stud.
CONTACT_LDU = 7.0

# Above this share of the smaller part's volume, a part is not overlapping its
# neighbour so much as inside it. Only ever used to word the report - never to
# decide whether there is a defect, which is what depth is for.
BURIED_FRACTION = 0.75

# What a fix is allowed to move by: a stud sideways, a plate vertically.
STUD = 20.0
PLATE = 8.0

# --------------------------------------------------------------------------
# How much shared plastic is too much
#
# ``occupancy.SHARED_LDU3`` is an absolute volume, and an absolute volume makes
# the check's sensitivity a function of how big the parts are. That was the
# bug this section fixes, and it showed up exactly where it had to: on slopes.
#
# A slope's solid is about half its bounding box, so an overlap involving one
# produces about half the shared plastic that the same wrongness between two
# plain bricks would. Put a small part on the other side of it and the number
# falls again. Measured:
#
#     a 1x1 brick buried inside a 2x2 slope        1009 ldu3   not reported
#     a 1x1 brick half a stud into a 2x2 slope      660 ldu3   not reported
#     a cheese slope sunk into a 2x4 brick          289 ldu3   not reported
#
# All three are unbuildable and all three came back "nothing overlaps",
# because 1,200 is the right number for two bricks and far too big for these.
#
# What separates them is not the volume but the *share*: how much of the
# smaller part's own plastic is inside the other one. On the same cases -
#
#     correct builds        at most  2.2% of the smaller part
#     the three defects     at least 12.5%
#
# - which is a 5.7x band where the absolute reading has 1.3x. So a pair is also
# a defect when it shares more than SHARED_FRACTION of the smaller part.
#
# The share on its own is not enough, and the case that proves it is a
# *correct* one: a 1x1 plate carried on a bracket's upstand shares 435 cubic
# LDU with the bracket, which is 19.7% of the plate - a bigger share than any
# of the three defects above. Nothing is wrong with it. The stud of the upstand
# sits inside the plate's tube, and at 1 LDU voxels with one voxel of erosion
# that interface leaves a skin behind; on a part as small as a 1x1 plate the
# skin is a fifth of the whole part.
#
# So the share decides only above an absolute floor, and the floor is set by
# that measurement rather than by taste:
#
# * **FRACTION_MIN_LDU3 = 600** clears the bracket's 435 with room, and clears
#   the corpus's legitimate small-part pairs (16 and 48 cubic LDU at 27% and
#   11%) by an order of magnitude.
# * **Both parts must seat on studs.** Almost every remaining false positive was
#   a Technic pin, axle, gear or bar - parts that thread *through* what they
#   connect to, whose fit is drawn as an interference and which are small
#   enough that the skin is a large share. They keep the absolute rule alone,
#   which is what `catalog.seats_on_studs` is already used for in `_shapes`.
#
# What the floor costs, and it is a real cost: the cheese slope at 289 cubic
# LDU stays missed. It sits below every value a correct connection can also
# reach, so no threshold on these two numbers separates it - see the KNOWN GAP
# in run_agent.py's checks, where it is recorded rather than quietly dropped.
# Catching it needs a measurement that tells a connection interface from an
# interpenetration, which the voxel count does not.
#
# Cost of the rule over 150 official sets, which are correct by construction:
# 9 reports before, 11 after. Both additions are the same pair in one 1962
# model hand-converted to LDraw - a 2x1 slope sharing 1,049 cubic LDU with a
# 1x4 plate, which is more likely a fault in that file than a fault in this.
#
# It only ever *adds* reports: the allowance is capped at SHARED_LDU3, so a
# pair reported before this existed is still reported.
SHARED_FRACTION = 0.10
FRACTION_MIN_LDU3 = 600.0

# --------------------------------------------------------------------------
# How THICK the shared plastic is, as opposed to how much of it there is
#
# Both rules above are volumes, and a volume is the wrong shape for the thing
# they are trying to exclude. What a correct pair shares is the quantisation
# skin along the face where the two parts meet - and a skin is a *surface*.
# occupancy.py says so in as many words: "that skin scales with the area they
# touch". Compared against a constant, the same skin therefore reads as
# harmless on a small contact and alarming on a big one, and a real
# interpenetration between two small parts reads as harmless full stop.
#
# That is not hypothetical. Across the 107 project models on disk, 739 pairs
# overlapped deeper than a legal engagement and were forgiven by the volume
# rules, and among them:
#
#     two 4x4 inverted dishes                 824 ldu3 / 1200 allowed   20 LDU deep
#     two 1x3 33-degree slopes                793 ldu3 / 1033 allowed   20 LDU deep
#     a 2x4 brick and a 2x2 45-degree slope   765 ldu3 / 1095 allowed   20 LDU deep
#     two cheese slopes                       572 ldu3 /  600 allowed   15.6 LDU deep
#
# Twenty LDU is a whole stud. Every one of those is two parts a full stud
# inside each other, and every one came back "nothing overlaps" - because the
# parts are small enough that a full stud of interpenetration still does not
# reach an allowance sized for two 2x4 bricks.
#
# So the shared volume is also divided by the area of the face the two parts
# present to each other - the two overlap axes that are not the shallowest -
# giving the MEAN THICKNESS of the shared plastic in LDU. A skin is thin by
# definition however wide it is; an interpenetration is thick.
#
# 0.5 LDU is half a voxel at occupancy.RESOLUTION. Calibrated, not chosen:
# every pair overlapping deeper than CONTACT_LDU was measured across 120
# official sets (correct by construction) and the 107 project models.
#
#     threshold   new reports in 120 official sets   of 40 known defects caught
#     0.30                                      12                          38
#     0.40                                       9                          38
#     0.45                                       4                          38
#     0.50                                       4                          38
#     0.60                                       4                          36
#     0.70                                       4                          33
#
# 0.45-0.60 is a plateau at four, so the value sits in the middle of it rather
# than on either edge. The four are a 1x1 round plate inside a ladder plate, a
# headlight brick inside a wheel-clip plate and a cheese slope inside a grille
# tile, twice - near-misses in hand-converted files of the same kind the corpus
# was already known to contain, rather than a class of correct build this
# refuses.
#
# Two gates keep it there, and both were measured rather than assumed:
#
# * **Both parts must seat on studs.** Everything this rule would otherwise
#   have added is a part that threads *through* what it connects to - a Technic
#   pin, a gear on an axle, a tyre round a rim, a bar through a minifigure's
#   head - where an interference fit is how the part is drawn. Without this
#   gate the same threshold adds 14 rather than 4.
# * **Both parts must be real catalogue entries.** A "~Moved to" redirect or an
#   "=" alias carries the geometry of nothing, and seven of the worst pairs in
#   the official corpus were one retired plate number against its replacement.
#
# It only ever adds reports: a pair already over one of the volume rules is
# still over it.
SHARED_SKIN_LDU = 0.5

# And a floor under the whole rule, because thickness alone does not separate
# the one case this project has pinned as a must-pass.
#
# A 1x1 plate carried on a bracket's upstand shares 435 cubic LDU across a
# contact face of only 240, which is a skin 1.81 LDU thick - thicker than any
# of the real defects above, all of which are between 0.29 and 1.43. The reason
# is the one already written down at FRACTION_MIN_LDU3: the upstand's stud sits
# inside the plate's tube, and on a part as small as a 1x1 plate the voxel skin
# left by that interface is a large share of the whole part. Normalising by
# contact area does not help, because the contact area is small for the same
# reason the skin is thick.
#
# What does separate them is the plain volume. Measured:
#
#     the bracket carrying a plate, correct        435 ldu3
#     the smallest of the eight real defects       562 ldu3
#
# so 500 sits between them. It is a narrow band - 1.29x, where the rest of this
# file works with bands of five and nine - and it is narrow because both
# quantities are near the resolution limit of a 1 LDU voxel grid. It is set
# here rather than hidden inside the skin number so that the tightness is
# visible, and `run_agent`'s checks pin both sides of it.
SHARED_SKIN_MIN_LDU3 = 500.0


# A box tells you nothing about what is inside it. A tyre's box contains the
# whole wheel, a helmet's contains the head, a window frame's contains its
# pane - and in every one of those the parts are correctly assembled and share
# no plastic at all. Real sets are full of them, so a check that trusts boxes
# alone reports hundreds of defects in models that are known to be right.
#
# What a box does describe faithfully is a plain rectangular brick or plate:
# solid, stud-topped, its bounding box its actual shape. Those are what the
# agent builds with, and where it buries one part inside another. So two parts
# are only ever judged against each other when the catalogue says both are
# that shape - everything else is left to the connectivity checker.
_SOLID_KINDS = ("brick", "plate")

# Words that mean the part does not fill its own box: an L, a wedge, a curve,
# a hole. Each of these was a false positive against a real set before it was
# listed here.
_NOT_A_BOX = (
    " with ", "corner", "wedge", "slope", "sloped", "curved", "curve", "arch",
    "cylinder", "cone", "dish", "bow", "angle", "fence", "grille",
    "lattice", "window", "door", "panel", "wing", "cockpit", "windscreen",
    # An L, and the corner of the L is empty. A bracket's box encloses the
    # space its own upstand holds parts *in*, so every part it is doing its job
    # on reads as buried inside it. 41682 alone was a third of every overlap
    # left in the reference corpus, against plates it was correctly carrying.
    # Most brackets were already exempt here by accident - their boxes are not
    # a whole number of studs, so `part_geometry` gave them no footprint - and
    # 41682's happens to be, which is not a distinction worth having.
    "bracket",
    # A curved shell over a wheel: the arch underneath is where the wheel goes.
    "mudguard",
    # Hollow bodies: the box is the outside of a shell, and everything stowed
    # inside it is correctly inside the box and touching none of the plastic.
    # Between them these two words name four parts in the whole catalogue -
    # three boat bases and one folding case - so the exemption is as narrow as
    # the problem is.
    "boat", "container",
)

# Round bricks and plates are the exception this list used to get wrong.
#
# "round" sat in _NOT_A_BOX above, which exempted every one of them from
# collision checking entirely - and a round brick is not like a tyre or a
# window frame. It is a *solid cylinder*: it fills its box everywhere except
# the four corners. Exempting it let a canopy of twenty 2x2 round bricks be
# placed on a one-stud pitch, each pair sharing a full stud of plastic, and a
# 2x4 brick be buried inside three of them - and `validate_model` answered
# "nothing overlaps", because none of those pairs was ever looked at.
#
# So they are checked, as cylinders rather than as boxes: the shape is shrunk
# to the largest square that fits inside the circle, half a side being
# ``r / sqrt(2)``. That square is entirely inside the real part, so anything
# overlapping it is genuinely shared plastic and no false alarm can come out of
# the shrink. Two round bricks a legal two studs apart clear each other by
# 11 LDU; the same two at one stud apart share 8, which is over CONTACT_LDU and
# reported.
#
# Cones, dishes and anything with a hole in it stay exempt above: they taper or
# are hollow, and one inscribed square does not describe them at any height.
#
# Measured before it landed, the same way the volume test was measured out: 170
# official sets chosen for being full of round parts were checked with the rule
# and without it. One set changed - 21047 Las Vegas, three pairs - and all three
# are a 1x1 round plate sharing a level with a plate it half sits inside, which
# is not buildable as boxes either and was invisible only because round parts
# were exempt wholesale. Against the build that prompted this, the same rule
# reports 32 overlaps, the worst of them 28 LDU deep.
_ROUND_SOLID = "round"
_INSCRIBED = 1.0 / math.sqrt(2.0)

# Qualifiers that describe a round part's *stud or top face* and say nothing
# about its body, which is still a full cylinder underneath.
#
# The " with " in _NOT_A_BOX is what catches a part carrying something - a clip,
# a handle, an axlehole - and it also caught these, which are the commonest
# round parts there are: `3062b` "Brick 1 x 1 Round with Hollow Stud" is what
# this agent builds every tree trunk out of, sixty at a time. A hollow stud is
# a hollow *stud*; the brick under it is as solid as any other.
_SURFACE_ONLY = ("hollow stud", "solid stud", "open stud", "groove",
                 "reinforced", "stud notch")


def _shapes(names):
    """What shape each of these parts is: ``{name: "box" | "cylinder" | None}``.

    None means the box does not describe the part and it is left to the
    connectivity checker. "cylinder" means it does, once shrunk - see
    ``_ROUND_SOLID``.
    """
    catalogue = {(row.get("part_id") or "").lower(): row
                 for row in catalog.load_catalog()}
    shapes = {}
    for name in names:
        row = catalogue.get((name or "").removesuffix(".dat").lower())
        geometry = catalog.part_geometry(row) if row else None
        description = ((row or {}).get("description") or "").lower()

        # A round part whose only disqualifier is a stud-or-surface qualifier
        # is still a cylinder: judge it on the rest of the list.
        forbidden = _NOT_A_BOX
        if (_ROUND_SOLID in description
                and any(word in description for word in _SURFACE_ONLY)):
            forbidden = tuple(w for w in _NOT_A_BOX if w != " with ")

        usable = bool(
            geometry
            and geometry["kind"] in _SOLID_KINDS
            and geometry["width_studs"] and geometry["depth_studs"]
            and not any(word in description for word in forbidden)
            # a retired number redirecting to its replacement: the row is a
            # signpost, and the geometry on it belongs to nothing
            and not description.startswith("~")
            and (row.get("category") or "") not in ("Moved", "Obsolete")
            # A part that does not seat on studs is not a box of solid plastic
            # however its bounding box measures. A Technic axle is 12 LDU tall
            # and so reads as a "plate" here, and it is a cross-section shaft
            # that *threads through* the holes in every beam it passes - which
            # is exactly the overlap this check would otherwise report. Same
            # for a pin in its hole and a bar in its clip. See
            # catalog.seats_on_studs.
            and catalog.seats_on_studs(name) is not False
        )
        if not usable:
            shapes[name] = None
        elif _ROUND_SOLID in description:
            # Only where the part is as wide as it is deep. "Plate 1 x 2 Round"
            # ends in two half-circles, not one, and an inscribed square across
            # the long axis would cut away plastic that is really there.
            shapes[name] = ("cylinder"
                            if geometry["width_studs"] == geometry["depth_studs"]
                            else None)
        else:
            shapes[name] = "box"
    return shapes


def _solid_shapes(names):
    """Which of these parts have a box worth judging. Back-compatible view."""
    return {name: kind is not None for name, kind in _shapes(names).items()}


def _cylinder_axis(instance):
    """Which world axis a round part's axis of symmetry points along.

    A round brick is drawn standing up, so its axis is local Y; an axis-aligned
    rotation sends that to one of the three world axes. The matrix is row-major,
    so local Y maps to its second column.
    """
    matrix = list(instance.matrix)
    if len(matrix) != 9:
        return 1
    column = (matrix[1], matrix[4], matrix[7])
    best = max(range(3), key=lambda i: abs(column[i]))
    return best


def _shrink_to_cylinder(box, axis):
    """The largest square prism that fits inside a cylinder's bounding box.

    Everything in the returned box is really plastic, so an overlap against it
    is real. The two axes across the cylinder come in by ``r/sqrt(2)``; the one
    along it is untouched, because the part fills its box completely that way.
    """
    (low_x, low_y, low_z), (high_x, high_y, high_z) = box
    lows, highs = [low_x, low_y, low_z], [high_x, high_y, high_z]
    for index in range(3):
        if index == axis:
            continue
        centre = (lows[index] + highs[index]) / 2.0
        half = (highs[index] - lows[index]) / 2.0 * _INSCRIBED
        lows[index], highs[index] = centre - half, centre + half
    return (tuple(lows), tuple(highs))


def _axis_aligned(instance, tolerance=1e-3):
    """Whether the part sits square to the grid.

    A rotated part's bounding box is bigger than the part - a plate turned 45
    degrees claims a box 41% wider than itself - so the box stops describing
    the shape and any overlap read from it is a guess. Those are left alone.
    """
    for value in instance.matrix:
        if abs(value) > tolerance and abs(abs(value) - 1.0) > tolerance:
            return False
    return True


def _catalogued(name):
    """Whether the catalogue describes this part rather than pointing at another.

    A "~Moved to 3023b" row and an "=Plate 1 x 1 Round" alias are signposts:
    the number is real, the description belongs to the part it redirects to,
    and the geometry belongs to nobody. ``_shapes`` already refuses to judge
    them; so does the skin rule, for the same reason.
    """
    row = catalog.get_part((name or "").removesuffix(".dat"))
    if not row:
        return False
    description = (row.get("description") or "")
    return bool(description) and not description.startswith(("~", "=")) \
        and (row.get("category") or "") not in ("Moved", "Obsolete")


def _skin_ldu(overlap, volume):
    """Mean thickness of the shared plastic over the face the parts share.

    The shallowest overlap axis is how far they are into each other; the other
    two are the face they present to each other. Dividing by that area turns a
    volume - which says as much about how big the parts are as about how wrong
    they are - into a depth, which does not. See SHARED_SKIN_LDU.
    """
    face = sorted(overlap)[1] * sorted(overlap)[2]
    return volume / face if face > 0 else 0.0


def _too_thick(inst_a, inst_b, overlap, volume):
    """Whether the shared plastic is an interpenetration rather than a skin."""
    if volume < SHARED_SKIN_MIN_LDU3:
        return False    # below the bracket's own 435 - see the note
    if min(overlap) <= CONTACT_LDU:
        # Outside the population this was calibrated on: only pairs deeper than
        # a legal engagement were measured, and a plate correctly on a brick is
        # a wide face with nothing behind it.
        return False
    names = (inst_a.src.part_name, inst_b.src.part_name)
    if any(catalog.seats_on_studs(n) is False for n in names):
        return False    # threads through rather than sits on - see the note
    if not all(_catalogued(n) for n in names):
        return False
    return _skin_ldu(overlap, volume) > SHARED_SKIN_LDU


def _volume(box):
    (minx, miny, minz), (maxx, maxy, maxz) = box
    return max(maxx - minx, 0.0) * max(maxy - miny, 0.0) * max(maxz - minz, 0.0)


def _centre(box):
    (minx, miny, minz), (maxx, maxy, maxz) = box
    return ((minx + maxx) / 2, (miny + maxy) / 2, (minz + maxz) / 2)


def _snap(depth, axis):
    """The shortest legal move that resolves an overlap of `depth` on this axis.

    A fix has to land back on the grid or it trades a collision for a
    misalignment, so it is always whole studs sideways, whole plates
    vertically.

    Sideways the parts have to end up clear of each other. Vertically they must
    not: a part above another belongs *on* it, engaged by its studs, so the
    move that fixes a brick sunk into the one below is the one that leaves
    exactly that engagement behind - not the one that lifts it clear into the
    air.
    """
    if axis == "y":
        step, wanted = PLATE, max(0.0, depth - STUD_ENGAGEMENT)
    else:
        step, wanted = STUD, depth
    steps = int(wanted / step) + (1 if wanted % step > 1e-6 else 0)
    return step * max(1, steps)


def _same_course(box_a, box_b, tolerance=1.0):
    """Whether both parts sit at the same height, top and bottom.

    Two bricks level with each other are a course of a wall, not a stack, so
    whatever went wrong between them went wrong sideways.
    """
    return (abs(box_a[0][1] - box_b[0][1]) <= tolerance
            and abs(box_a[1][1] - box_b[1][1]) <= tolerance)


def _describe(part_a, part_b, overlap, box_a, box_b):
    """One overlap, as the fact and the fix."""
    depth, axis = min(zip(overlap, "xyz"))

    # The cheapest way apart is not always the right way. Two bricks in one
    # course overlapping half a brick escape most cheaply upwards - but lifting
    # one onto the other builds a different model, where sliding it along
    # builds the intended one. So parts on the same level are separated on the
    # level they share.
    if _same_course(box_a, box_b):
        depth, axis = min((overlap[0], "x"), (overlap[2], "z"))
    volume = overlap[0] * overlap[1] * overlap[2]
    smaller = min(_volume(box_a), _volume(box_b)) or 1.0
    fraction = volume / smaller

    centre_a, centre_b = _centre(box_a), _centre(box_b)
    index = "xyz".index(axis)
    # move the second part away from the first, along its own side of the axis
    direction = 1 if centre_b[index] >= centre_a[index] else -1
    move = _snap(depth, axis) * direction

    return {
        "depth_ldu": round(depth, 1),
        "axis": axis,
        "overlap_ldu": {"x": round(overlap[0], 1),
                        "y": round(overlap[1], 1),
                        "z": round(overlap[2], 1)},
        "shared_volume_pct": round(fraction * 100),
        "fraction": fraction,
        "suggested_move": {"axis": axis, "ldu": round(move, 1)},
    }


def _same_place(box_a, box_b, tolerance=1.0):
    centre_a, centre_b = _centre(box_a), _centre(box_b)
    return all(abs(p - q) <= tolerance for p, q in zip(centre_a, centre_b))


def _part(inst):
    where = " < ".join(reversed(inst.path)) if inst.path else inst.src.submodel
    name = (inst.src.part_name or "").removesuffix(".dat")
    described = catalog.get_part(name)
    return {
        "line": inst.src.line_no,
        "part": inst.src.part_name,
        "description": (described or {}).get("description"),
        "position": [round(v, 1) for v in inst.pos],
        "submodel": where if where and where != "__main__" else None,
    }


def build_scene(coll, model, library_root):
    """Every placed part with its true world box, and which of them are boxes.

    The one description of the model that both the report and the auto-fix work
    from, so a move judged clean here is a move the report agrees is clean.
    Returns ``(flat, boxes, solid)``, ``boxes`` parallel to ``flat``.
    """
    cache = {}
    flat, _ = coll.flatten_model(model)
    shapes = _shapes({inst.src.part_name for inst in flat})

    boxes = []
    for inst in flat:
        points = coll.compute_part_points(inst.src.part_name, library_root,
                                          cache, model=model)
        # True boxes, unshrunk. The checker's shrink factor exists to hide
        # legitimate interlock, and it hides a fifth of every part along with
        # it - a brick can be a third of a stud inside another and go unseen.
        box = coll.world_aabb(inst, points or coll.GENERIC_POINTS, 1.0)
        # A round brick's box is a square and the part is a circle inside it.
        # Judged as the box it would collide with its neighbours' corners;
        # judged as the inscribed square it collides only where there really is
        # plastic. Only for placements square to the grid - the axis of a part
        # turned to some arbitrary angle is not one of the world's.
        if shapes.get(inst.src.part_name) == "cylinder" and _axis_aligned(inst):
            box = _shrink_to_cylinder(box, _cylinder_axis(inst))
        boxes.append(box)

    solid = {name: kind is not None for name, kind in shapes.items()}
    return flat, boxes, solid


def classify_pair(inst_a, inst_b, box_a, box_b, overlap, solid, measure=None):
    """What two overlapping boxes are to each other.

    Returns ``(kind, detail)``. ``kind`` is None when the overlap is a contact -
    parts touching as they should - "unchecked" when nothing here can say, and
    one of "duplicate", "buried" or "overlap" when it is two parts in one space.

    ``measure`` is a callable taking the two instances and returning
    ``(shared_ldu3, allowed_ldu3)``, or None where it cannot tell - see
    ``measurer``. When it is given it decides, and every exemption below is
    bypassed: it measures the parts rather than guessing at them from their
    descriptions, so a slope, a bracket and a dish are as answerable as a 2x4
    brick. Without it the old box-and-blocklist reading stands, which is what
    any caller that has not been passed a library still gets.
    """
    detail = _describe(inst_a, inst_b, overlap, box_a, box_b)

    if measure is not None:
        measured = measure(inst_a, inst_b)
        if measured is None:
            # One of them has no geometry to measure. Deep box overlaps between
            # parts nobody could measure are the case that used to pass in
            # silence; they are named now rather than dropped.
            if detail["depth_ldu"] > CONTACT_LDU:
                return "unchecked", detail
            return None, detail
        volume, allowed = measured
        detail["shared_ldu3"] = round(volume, 1)
        detail["allowed_ldu3"] = round(allowed, 1)
        detail["shared_skin_ldu"] = round(_skin_ldu(overlap, volume), 2)
        # Two readings of the same measurement, and a pair has to clear both.
        # The allowance asks how much plastic is shared; the skin asks how
        # thickly - which is what tells a full stud of interpenetration between
        # two small parts from the quantisation edge along a wide correct
        # contact. See SHARED_SKIN_LDU for why one of them is not enough.
        if volume <= allowed and not _too_thick(inst_a, inst_b, overlap, volume):
            return None, detail
        if (inst_a.src.part_name == inst_b.src.part_name
                and _same_place(box_a, box_b)):
            return "duplicate", detail
        return ("buried" if detail["fraction"] >= BURIED_FRACTION
                else "overlap"), detail

    # The same part twice in the same place, and both of them a shape whose box
    # means something.
    #
    # This used to be "wrong whatever its shape", and that clause was the single
    # largest source of false collisions in the reference corpus: 613 of 616.
    # A *flexible* element - a rubber band, a rope, a hose, a chain - is not one
    # part in LDraw. It is a generated run of dozens of copies of one primitive,
    # each a fraction of a degree round from the last and each therefore within
    # a millimetre of its neighbour's centre. Set 8001's rubber band alone is
    # 1,664 pairs of `box4o8a` "duplicating" each other, and every one of them
    # is the band being drawn correctly.
    #
    # Gating on `solid` is what tells the two apart without knowing anything
    # about bands: a primitive and a part that does not seat on studs are both
    # already excluded there, and a real brick placed twice in one spot still
    # is not.
    duplicate = (inst_a.src.part_name == inst_b.src.part_name
                 and solid.get(inst_a.src.part_name)
                 and _same_place(box_a, box_b))
    deep = (solid.get(inst_a.src.part_name)
            and solid.get(inst_b.src.part_name)
            and _axis_aligned(inst_a) and _axis_aligned(inst_b)
            and detail["depth_ldu"] > CONTACT_LDU)

    if duplicate:
        return "duplicate", detail
    if not deep:
        return None, detail
    return ("buried" if detail["fraction"] >= BURIED_FRACTION else "overlap"), detail


def _allowance(inst_a, inst_b, library_root, coll, store, model):
    """How much shared plastic this particular pair is allowed.

    ``SHARED_LDU3`` where the fractional reading does not apply, and the
    smaller of the two where it does - see SHARED_FRACTION above for the
    reasoning and the measurements.
    """
    names = (inst_a.src.part_name, inst_b.src.part_name)
    if any(catalog.seats_on_studs(n) is False for n in names):
        # Threads through what it connects to rather than sitting on it. The
        # fractional rule is not for these; the absolute one still is.
        return occupancy.SHARED_LDU3

    volumes = [occupancy.core_volume(n, library_root, coll, store, model)
               for n in names]
    if any(v is None or v <= 0 for v in volumes):
        return occupancy.SHARED_LDU3

    # A share of the smaller part, floored so that a share of a *tiny* part
    # cannot fall into the quantisation skin, and capped so this can only ever
    # be stricter than the absolute rule and never looser.
    return min(occupancy.SHARED_LDU3,
               max(FRACTION_MIN_LDU3, SHARED_FRACTION * min(volumes)))


def measurer(coll, library_root, model=None, cache=None):
    """A callable measuring what two placed parts share, and what they may.

    Returns ``(shared_ldu3, allowed_ldu3)``, or None where at least one of the
    pair has no geometry to measure - which is a third answer and the caller
    has to keep it that way.

    The allowance travels with the measurement because it is a property of the
    pair rather than a constant: see SHARED_FRACTION. Everything downstream
    compares the two rather than comparing against a module-level number.

    None instead of a callable when there is no library to read geometry out
    of, which puts every caller back on the box reading. The cache is
    per-model rather than global: a solid is a few hundred kilobytes and a
    long-lived process building many models has no reason to hold the parts of
    all of them.
    """
    if not library_root:
        return None
    store = {} if cache is None else cache

    def measure(inst_a, inst_b):
        volume = occupancy.shared_volume(inst_a, inst_b, library_root, coll,
                                         store, model)
        if volume is None:
            return None
        return volume, _allowance(inst_a, inst_b, library_root,
                                             coll, store, model)

    return measure


def inspect(coll, model, library_root, max_listed=8, scene=None, measure=None):
    """Classify every overlap in a flattened model.

    Returns the counts, the overlaps worth acting on worst first, and how many
    deep overlaps nothing could judge.

    ``scene`` is a ``build_scene`` result the caller already has. Passing it in
    saves building it twice: validation wants the same world boxes to work out
    what is standing on the ground and what is hanging in the air.

    ``measure`` measures shared plastic - see ``measurer``. One is made here
    when the caller did not pass one, so that the accurate reading is what a
    plain call gets rather than something you have to know to ask for.
    """
    flat, boxes, solid = scene or build_scene(coll, model, library_root)
    if measure is None:
        measure = measurer(coll, library_root, model)
    pairs = list(zip(flat, boxes))
    by_instance = {id(inst): box for inst, box in pairs}
    contacts, found, unchecked = 0, [], []

    for inst_a, inst_b, overlap in coll.find_collisions(pairs,
                                                        same_space_only=False):
        box_a, box_b = by_instance[id(inst_a)], by_instance[id(inst_b)]
        kind, detail = classify_pair(inst_a, inst_b, box_a, box_b, overlap,
                                     solid, measure=measure)
        if kind is None:
            contacts += 1
            continue
        if kind == "unchecked":
            # Deep enough to matter, and no geometry to settle it with. Said
            # out loud: this is the pile the old check emptied in silence, and
            # "we did not look" must never again arrive as "nothing is wrong".
            unchecked.append({"a": _part(inst_a), "b": _part(inst_b),
                              **detail})
            continue

        duplicate = kind == "duplicate"
        buried = detail.pop("fraction") >= BURIED_FRACTION
        if duplicate:
            kind, advice = "duplicate", (
                f"{inst_a.src.part_name} is placed twice in the same spot - "
                f"delete the one on line {inst_b.src.line_no}")
        elif buried:
            kind, advice = "buried", (
                f"line {inst_b.src.line_no} sits inside line "
                f"{inst_a.src.line_no}: move it "
                f"{detail['suggested_move']['ldu']:+.0f} LDU on "
                f"{detail['axis']}, or delete it if it was meant to replace "
                f"the other")
        else:
            kind, advice = "overlap", (
                f"these two share {detail['depth_ldu']:.0f} LDU of solid "
                f"plastic along {detail['axis']} - move line "
                f"{inst_b.src.line_no} by {detail['suggested_move']['ldu']:+.0f} "
                f"on {detail['axis']}")

        found.append({"kind": kind, "a": _part(inst_a), "b": _part(inst_b),
                      "fix": advice, **detail})

    # worst first: what is most buried is most likely to be the real mistake
    found.sort(key=lambda c: (-c.get("shared_ldu3", 0.0),
                              -c["shared_volume_pct"], -c["depth_ldu"]))
    unchecked.sort(key=lambda c: -c["depth_ldu"])
    out = {
        "overlapping": len(found),
        "contacts": contacts,
        "overlapping_parts": found[:max_listed],
    }
    if unchecked:
        out["unchecked_deep_overlaps"] = len(unchecked)
        out["unchecked_overlap_parts"] = unchecked[:max_listed]
        out["unchecked_note"] = (
            "These pairs overlap by more than a stud's engagement and their "
            "shapes could not be measured, so nothing here can say whether "
            "they share plastic. It is NOT a report that they are fine. Look "
            "at the render before trusting them, and if a pair does look wrong "
            "move it apart on the stud grid.")
    return out
