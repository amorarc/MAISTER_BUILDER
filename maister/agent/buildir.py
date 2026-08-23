"""An executable build sequence, and the compiler that turns it into LDraw.

The builder writes LDraw lines by hand. Every coordinate in every model is a
number a language model typed out from a sentence, and the sentence came from
``plan_construction``, which says things like:

    "placements": "one plate 6 x 12 at x = 0, z = 0"

That is prose. Turning it into ``1 2 40 -216 0 1 0 0 0 1 0 0 0 1 3941.dat``,
five times, with the spacing right, is arithmetic - and it is where the builds
actually break. The tree that prompted this has five 2x2 round bricks in a row
at x = 40, 60, 80, 100, 120. A 2x2 brick is 40 LDU across. At a 20 LDU pitch
every pair shares a full stud of plastic, and the model is unbuildable.

Nobody chose that. It is a transcription error, and no amount of care in the
prompt removes a whole class of arithmetic from a model that is doing the
arithmetic in its head.

So the arithmetic moves here. The builder says *what* it wants placed:

    {"op": "row", "part": "3941", "colour": 2, "at": [40, -216, 0], "count": 5}

and this module looks 3941 up in the catalogue, finds it is 2 studs across,
and emits five parts at a 40 LDU pitch. **The spacing is not an input.** The
canopy bug is not a bug this can produce, because there is nowhere to type it.

# What this is and is not

This is the "generate an editable construction sequence, compile it with a real
kernel" pattern, in the only form that means anything for LEGO. There is no
sketch-and-extrude here and there never will be: a LEGO model is not swept
geometry, it is a bill of real parts on a lattice, and the catalogue of 5,879
elements *is* the primitive set. The op vocabulary below is therefore the one
the domain actually has - place, repeat along a line, repeat over a grid,
repeat upward - rather than one borrowed from mechanical CAD.

What does carry over exactly:

* **The sequence is the artifact.** The ops are stored beside the model, so a
  build is a program that can be re-run, not a wall of coordinates.
* **Topology apart from parameters.** An op says which part and how it repeats;
  the numbers it repeats by are derived, not stored. Changing a part changes the
  spacing automatically, because the spacing was never written down.
* **A fixed vocabulary beats freeform output.** Four ops the compiler
  understands completely, rather than lines it has to trust.

# It does not replace edit_model

Ops lay parts down in the regular arrangements that make up most of a build.
Moving one brick, recolouring a line, deleting a part someone changed their
mind about - that is line surgery and ``edit_model`` remains the tool for it.
A build normally uses both.
"""

import json
import math

from . import catalog

# The stud lattice, in LDU.
STUD = 20.0
# Positions must land on a multiple of this. 10 rather than 20 because a jumper
# plate legitimately puts a part on a half stud.
LATTICE = 10.0

# A single op may not place more than this. A count with a typo in it is the
# one way this tool could produce a hundred thousand parts and a file nobody
# can open, and no real build needs more in one op.
MAX_PER_OP = 400
MAX_TOTAL = 2000

# Ops that place parts. Everything in this tuple reaches the compile loop.
LEAF_OPS = ("place", "row", "grid", "stack", "ring", "mirror", "wall", "box",
            "fill")
# Ops that place nothing themselves and instead say what happens to the ops
# inside them. Expanded away before anything is compiled - see `_expand`.
GROUP_OPS = ("repeat", "reflect", "define", "call")

OPS = LEAF_OPS + GROUP_OPS

# How many leaf ops a call may expand to, and how deep the groups may nest.
# The same reasoning as MAX_PER_OP: `{"op": "repeat", "times": 9999}` round a
# `repeat` of a `repeat` is one typo away from a file nobody can open, and the
# per-op and per-call part caps below do not see it coming because each
# individual leaf is small.
MAX_EXPANDED_OPS = 600
MAX_GROUP_DEPTH = 6

# --------------------------------------------------------------------------
# `ring` and `mirror`
#
# The four ops above were the ones the domain obviously has. These two are the
# ones it turned out to need, and both were found the same way: by reading what
# the builder actually wrote. Across 43 models, two thirds of every op was
# `place` - this module being used as a typewriter - and inside those hand-laid
# runs the same two shapes kept appearing.
#
# **A ring.** Three groups of four parts, each at a different right angle,
# arranged round a centre: the slopes that finish a tower roof. Two of those
# three groups are interpenetration defects, sitting a full stud inside each
# other. That is the canopy bug again in a different costume - the spacing was
# typed rather than derived - and it is not expressible in `row` or `grid`,
# because what varies round a ring is the rotation as well as the position.
#
# **A mirror.** Thirty hand-placed symmetric pairs. The acceptance requirements
# ask for symmetry by name and call a detail landing a stud off on one side
# "the difference between a model that reads as finished and one that reads as
# a first draft" - and there was no op that produced one. Mirroring by hand is
# exactly where that error comes from.
#
# Neither op mirrors the *geometry*. A negative determinant is a part that
# cannot be moulded, so the reflected copy is turned instead: the rotation that
# reads as the mirror image of a right-angle turn is another right-angle turn.
# See `_mirror_turn`.

# Which way a ring's first part sits from the centre, as a unit vector in
# (x, z). -Z is the direction an unturned part looks along, so index 0 is at
# the front and the rest go round from there. What matters is not the handedness
# but that each part is turned by the same angle its position is - which is
# what makes every one of them face outward.
_RING_START = (0.0, -1.0)

# Rotation matrices, row-major, by (axis, degrees). Only right angles: an
# arbitrary angle takes a part off the lattice, which is the one thing every
# other check in this project exists to prevent. These are the same matrices
# the reference corpus uses - see build_technique_notes.py, which counts them.
_ROTATIONS = {
    ("x", 0): (1, 0, 0, 0, 1, 0, 0, 0, 1),
    ("x", 90): (1, 0, 0, 0, 0, -1, 0, 1, 0),
    ("x", 180): (1, 0, 0, 0, -1, 0, 0, 0, -1),
    ("x", 270): (1, 0, 0, 0, 0, 1, 0, -1, 0),
    ("y", 0): (1, 0, 0, 0, 1, 0, 0, 0, 1),
    ("y", 90): (0, 0, 1, 0, 1, 0, -1, 0, 0),
    ("y", 180): (-1, 0, 0, 0, 1, 0, 0, 0, -1),
    ("y", 270): (0, 0, -1, 0, 1, 0, 1, 0, 0),
    ("z", 0): (1, 0, 0, 0, 1, 0, 0, 0, 1),
    ("z", 90): (0, -1, 0, 1, 0, 0, 0, 0, 1),
    ("z", 180): (-1, 0, 0, 0, -1, 0, 0, 0, 1),
    ("z", 270): (0, 1, 0, -1, 0, 0, 0, 0, 1),
}


# --------------------------------------------------------------------------
# Transforms, and the group ops that carry them
#
# The eight ops above each place one part n times, and between them they cover
# a row, a grid, a stack, a ring, a pair and a course. What they cannot do is
# say "and again, four bricks higher", or "and the same thing on the other
# side", or "that window, six times" - because there is no way to talk about a
# *group* of ops at all.
#
# Measured across the 1,938 ops on disk, that gap is where the whole tool
# leaks: 81.6% of every op written is `place`. Not because the builder wants
# to type coordinates, but because the thing it is building - four identical
# courses of wall, a pair of wings, a row of windows - is a group, and the
# vocabulary had no word for one. `mirror` was called once in 1,938 ops for
# exactly this reason: mirroring a single part is rarely what anybody means,
# and mirroring an assembly is what symmetry actually is.
#
# So `repeat`, `reflect`, `define` and `call` place nothing themselves. They
# are expanded away before compiling into the leaf ops they contain, each
# carrying a rigid motion. Every leaf then goes through exactly the same
# geometry, the same phase snapping and the same grid check it always did,
# which is the property worth keeping: the compiler still checks every
# placement, and there is still nowhere to type a spacing.
#
# # Why a reflection is not a reflection
#
# A true mirror image has a negative determinant, and a part with a negative
# determinant was never moulded. So a reflected placement is *turned* instead,
# by conjugating its rotation:
#
#     R' = S · R · L
#
# where S flips the world axis being mirrored and L = diag(-1, 1, 1) is the
# part's own mirror plane. Both flips make the determinant positive again, and
# the result is the rotation that reads as the mirror image of R. For the
# right-angle turns about Y this reduces exactly to `_mirror_turn`, which is
# the same rule written out for the one case that already had it.
#
# The assumption underneath is the one `mirror` has always made: a part with a
# facing direction is symmetric about its own local x = 0. That is true of
# slopes, wedges, arches, tiles and brackets - the parts anyone reflects. It is
# not true of a handful of deliberately handed parts, which is why the reflected
# copy is a copy of the same part number rather than its left-handed twin.

_IDENTITY3 = (1, 0, 0, 0, 1, 0, 0, 0, 1)
# The part's own mirror plane, in part coordinates. See above.
_LOCAL_MIRROR = (-1, 0, 0, 0, 1, 0, 0, 0, 1)


class _Transform:
    """A rigid motion applied to everything a group op contains.

    ``linear`` is a row-major 3x3 of right angles and axis flips; ``translate``
    is applied after it; ``flipped`` says whether an odd number of reflections
    has been composed, which is what decides whether a part's rotation needs
    conjugating back to something mouldable.
    """

    __slots__ = ("linear", "translate", "flipped")

    def __init__(self, linear=_IDENTITY3, translate=(0.0, 0.0, 0.0),
                 flipped=False):
        self.linear = tuple(linear)
        self.translate = tuple(float(v) for v in translate)
        self.flipped = bool(flipped)

    @property
    def identity(self):
        return (self.linear == _IDENTITY3 and not self.flipped
                and not any(self.translate))

    def then(self, outer):
        """This motion, followed by ``outer``. Returns a new transform."""
        return _Transform(
            _matmul(outer.linear, self.linear),
            tuple(a + b for a, b in
                  zip(_matvec(outer.linear, self.translate), outer.translate)),
            self.flipped != outer.flipped)

    def point(self, position):
        return tuple(a + b for a, b in
                     zip(_matvec(self.linear, position), self.translate))

    def matrix(self, matrix):
        """A part's rotation, moved by this transform and still mouldable."""
        turned = _matmul(self.linear, tuple(matrix))
        return _matmul(turned, _LOCAL_MIRROR) if self.flipped else turned


IDENTITY = _Transform()


def _matmul(a, b):
    return tuple(sum(a[row * 3 + k] * b[k * 3 + col] for k in range(3))
                 for row in range(3) for col in range(3))


def _matvec(matrix, vector):
    return tuple(sum(matrix[row * 3 + k] * vector[k] for k in range(3))
                 for row in range(3))


class BuildError(ValueError):
    """An op that cannot be compiled, with what is wrong with it."""


class _Laid(Exception):
    """A macro op that has already produced its placements.

    `wall` and `box` resolve to many parts rather than one, so they leave the
    shared setup early. Raised and caught inside the compile loop only, which
    keeps the one path that emits lines rather than growing a second.
    """


# -- parts -------------------------------------------------------------------

def _geometry(part):
    """``(row, geometry)`` for a part id, or raise BuildError."""
    name = str(part or "").strip()
    if not name:
        raise BuildError("an op has no `part`")
    if name.lower().endswith(".dat"):
        name = name[:-4]

    row = catalog.get_part(name)
    if row is None:
        raise BuildError(
            f"`{name}` is not in the parts catalogue, so it does not exist - "
            f"search_parts for the shape you meant and use the part_id it "
            f"returns")

    # `get_part` hands back a summarised row that has already been through
    # `part_geometry`, so the measurements are on the row itself. Running
    # `part_geometry` over it again looks for the raw min_x/max_x columns,
    # finds none, and reports every part in the catalogue as unmeasured.
    geometry = {key: row.get(key) for key in
                ("bbox", "width_studs", "depth_studs", "kind",
                 "place_height_ldu", "has_top_studs")}
    if not geometry.get("bbox"):
        raise BuildError(
            f"`{name}` has no measured geometry in the catalogue, so its "
            f"spacing cannot be worked out - place it with edit_model instead")
    return row, geometry


def _rotation(spec):
    """``(axis, degrees, matrix)`` from an op's `rotate` field."""
    if spec in (None, 0, "0", ""):
        return "y", 0, _ROTATIONS[("y", 0)]

    axis, degrees = "y", spec
    if isinstance(spec, dict):
        axis = str(spec.get("axis") or "y").lower()
        degrees = spec.get("degrees", spec.get("angle", 0))
    try:
        degrees = int(round(float(degrees))) % 360
    except (TypeError, ValueError):
        raise BuildError(f"`rotate` must be a number of degrees, not {spec!r}")

    if axis not in ("x", "y", "z"):
        raise BuildError(f"`rotate.axis` must be x, y or z, not {axis!r}")
    if degrees % 90:
        raise BuildError(
            f"`rotate` must be a multiple of 90 degrees; {degrees} would take "
            f"the part off the stud grid. Use edit_model to place a part at a "
            f"hinge angle.")
    return axis, degrees, _ROTATIONS[(axis, degrees)]


def _extents(geometry, matrix):
    """The part's world-space size as ``(x, y, z)`` LDU, after rotation.

    Rotations here are all right angles, so this permutes the bounding box
    rather than approximating it. Snapped to the lattice because a bounding box
    runs a few tenths wide - a curved slope overhangs its own studs - and an
    unsnapped pitch would put every part after the first slightly off the grid.
    """
    box = geometry["bbox"]
    size = (box["x"][1] - box["x"][0],
            box["y"][1] - box["y"][0],
            box["z"][1] - box["z"][0])

    out = []
    for axis in range(3):
        # row `axis` of the matrix says which local axes feed this world axis
        total = sum(abs(matrix[axis * 3 + local]) * size[local]
                    for local in range(3))
        out.append(total)

    def snap(value):
        return max(LATTICE, round(value / LATTICE) * LATTICE)

    # Height is not snapped to the stud lattice: a plate is 8 LDU and a brick
    # 24, and rounding either to 10 would build a model that does not stack.
    return snap(out[0]), out[1], snap(out[2])


def _place_height(geometry, axis, degrees):
    """How far up the next part in a stack goes, in LDU.

    Upright, that is the catalogue's stacking height - 24 for a brick, 8 for a
    plate, which is the figure the whole project counts in. On its side it is
    the part's rotated height instead, since the thing being stacked is no
    longer the part's own top face.
    """
    if axis == "y" or degrees == 0 or degrees == 180:
        height = geometry.get("place_height_ldu")
        if height:
            return float(height)
    box = geometry["bbox"]
    size = (box["x"][1] - box["x"][0],
            box["y"][1] - box["y"][0],
            box["z"][1] - box["z"][0])
    matrix = _ROTATIONS[(axis, degrees)]
    return sum(abs(matrix[3 + local]) * size[local] for local in range(3))


# -- ops ---------------------------------------------------------------------

def _at(op):
    where = op.get("at") or op.get("position") or op.get("origin")
    if not isinstance(where, (list, tuple)) or len(where) != 3:
        raise BuildError(
            f"`at` must be [x, y, z] in LDU; got {where!r}")
    try:
        point = [float(v) for v in where]
    except (TypeError, ValueError):
        raise BuildError(f"`at` must be three numbers; got {where!r}")
    return point


def _colour(op):
    colour = op.get("colour", op.get("color"))
    if colour is None:
        raise BuildError(
            "every op needs a `colour` - an LDraw colour code. 16 means "
            "'inherit', which on a part resolves to whatever the viewer "
            "defaults to and is why models come out looking uncoloured.")
    try:
        return int(colour)
    except (TypeError, ValueError):
        raise BuildError(f"`colour` must be an LDraw colour code, not {colour!r}")


def _count(op, key="count"):
    try:
        count = int(op.get(key, 1))
    except (TypeError, ValueError):
        raise BuildError(f"`{key}` must be a whole number, not {op.get(key)!r}")
    if count < 1:
        raise BuildError(f"`{key}` must be at least 1, not {count}")
    if count > MAX_PER_OP:
        raise BuildError(
            f"`{key}` is {count}; one op may place at most {MAX_PER_OP} parts. "
            f"If that many really are wanted, split it into several ops.")
    return count


def _pitch(op, extents, index, geometry):
    """Centre-to-centre spacing along an axis, in LDU.

    Derived from the part, never from the op - which is the entire point of
    this module. ``gap_studs`` opens a deliberate gap; ``pitch_ldu`` overrides
    outright and is checked, because an override smaller than the part is the
    exact mistake this exists to prevent.
    """
    natural = extents[index]

    override = op.get("pitch_ldu")
    if override is not None:
        try:
            override = float(override)
        except (TypeError, ValueError):
            raise BuildError(f"`pitch_ldu` must be a number, not {override!r}")
        if override < natural - 0.01:
            raise BuildError(
                f"`pitch_ldu` of {override:g} is closer than the part is wide: "
                f"`{geometry['part_id']}` measures {natural:g} LDU along that "
                f"axis, so at {override:g} every pair would share "
                f"{natural - override:g} LDU of solid plastic. Leave "
                f"`pitch_ldu` out and the spacing is worked out for you.")
        if override % LATTICE:
            raise BuildError(
                f"`pitch_ldu` of {override:g} is not a multiple of {LATTICE:g}, "
                f"so the parts after the first would land off the stud grid")
        return override

    gap = op.get("gap_studs", 0)
    try:
        gap = float(gap)
    except (TypeError, ValueError):
        raise BuildError(f"`gap_studs` must be a number, not {gap!r}")
    return natural + gap * STUD


def _positions(op, extents, geometry):
    """Every position this op places a part at."""
    kind = op["op"]
    origin = _at(op)

    if kind == "place":
        return [tuple(origin)]

    if kind == "row":
        axis = str(op.get("axis", "x")).lower()
        if axis not in ("x", "z"):
            raise BuildError(
                f"`row.axis` must be x or z, not {axis!r} - a row going up is "
                f"a `stack`")
        index = 0 if axis == "x" else 2
        pitch = _pitch(op, extents, index, geometry)
        out = []
        for step in range(_count(op)):
            point = list(origin)
            point[index] += pitch * step
            out.append(tuple(point))
        return out

    if kind == "grid":
        counts = op.get("counts") or [op.get("count_x", 1), op.get("count_z", 1)]
        if not isinstance(counts, (list, tuple)) or len(counts) != 2:
            raise BuildError("`grid.counts` must be [along_x, along_z]")
        nx = _count({"count": counts[0]})
        nz = _count({"count": counts[1]})
        if nx * nz > MAX_PER_OP:
            raise BuildError(
                f"a {nx} x {nz} grid is {nx * nz} parts; one op may place at "
                f"most {MAX_PER_OP}")
        pitch_x = _pitch(op, extents, 0, geometry)
        pitch_z = _pitch(op, extents, 2, geometry)
        return [(origin[0] + pitch_x * i, origin[1], origin[2] + pitch_z * j)
                for i in range(nx) for j in range(nz)]

    if kind == "stack":
        height = op.get("_height")
        out = []
        for step in range(_count(op)):
            # -Y is up: each part sits on top of the one before it.
            out.append((origin[0], origin[1] - height * step, origin[2]))
        return out

    if kind in ("ring", "mirror"):
        # Both of these turn each copy as well as moving it, so they cannot
        # answer with positions alone. `_turned_positions` produces the pairs.
        return [point for point, _ in _turned_positions(op, extents, geometry)]

    raise BuildError(f"unknown op `{kind}` - the ops are {', '.join(OPS)}")


def _ring_radius(op, extents):
    """Centre-to-part distance for a ring, in LDU.

    Defaults to the part's own depth, which puts four parts edge to edge around
    a square - the arrangement that finishes a roof. Snapped to the lattice,
    because a radius off the grid takes every part in the ring off it.
    """
    given = op.get("radius_ldu")
    if given is None and op.get("radius_studs") is not None:
        try:
            given = float(op["radius_studs"]) * STUD
        except (TypeError, ValueError):
            raise BuildError(f"`radius_studs` must be a number, "
                             f"not {op['radius_studs']!r}")
    if given is None:
        given = extents[2]
    try:
        radius = float(given)
    except (TypeError, ValueError):
        raise BuildError(f"`radius_ldu` must be a number, not {given!r}")
    if radius <= 0:
        raise BuildError("a ring's radius must be more than 0")
    if radius % LATTICE:
        raise BuildError(
            f"a radius of {radius:g} LDU is not a multiple of {LATTICE:g}, so "
            f"the parts round the ring would land off the stud grid")
    return radius


def _mirror_turn(degrees, about):
    """The right-angle turn that reads as the mirror image of ``degrees``.

    A true reflection has a negative determinant, which is a part that was
    never moulded - so the far copy is *turned* instead. About the x = 0 plane
    a turn of t reads as -t; about z = 0 it reads as 180 - t. Both keep the
    part on the lattice and both keep the determinant positive.
    """
    return (-degrees if about == "x" else 180 - degrees) % 360


def _turned_positions(op, extents, geometry):
    """``[(position, degrees_about_y)]`` for the ops that turn each copy."""
    kind = op["op"]
    origin = _at(op)
    base = int(op.get("_degrees") or 0)

    if kind == "ring":
        count = _count(op)
        # 2 and 4 are the whole list, because 360/count has to be a right
        # angle: a ring of three is 120 degrees apart, which takes every part
        # off the stud grid. It is not an arbitrary restriction, it is the
        # lattice - and 3 divides 360 evenly, so the test has to be the angle
        # rather than the division.
        if count not in (2, 4):
            raise BuildError(
                f"a ring of {count} puts {360 / count:g} degrees between "
                f"parts, which is not a right angle and would take them off "
                f"the stud grid - a ring is 2 or 4 parts. For anything else, "
                f"place them individually with `place`.")
        radius = _ring_radius(op, extents)
        out = []
        for index in range(count):
            turn = (base + index * (360 // count)) % 360
            # the start direction, turned by the same angle the part is
            radians = math.radians(turn)
            cos, sin = round(math.cos(radians), 9), round(math.sin(radians), 9)
            dx = _RING_START[0] * cos + _RING_START[1] * sin
            dz = -_RING_START[0] * sin + _RING_START[1] * cos
            out.append(((origin[0] + dx * radius, origin[1],
                         origin[2] + dz * radius), turn))
        return out

    # mirror
    about = str(op.get("about", "x")).lower()
    if about not in ("x", "z"):
        raise BuildError(
            f"`mirror.about` must be x or z - the plane the pair is symmetric "
            f"about - not {about!r}")
    try:
        plane = float(op.get("plane", 0.0))
    except (TypeError, ValueError):
        raise BuildError(f"`mirror.plane` must be a number, not {op.get('plane')!r}")

    index = 0 if about == "x" else 2
    far = list(origin)
    far[index] = 2 * plane - origin[index]
    if abs(far[index] - origin[index]) < 0.01:
        raise BuildError(
            f"this part already sits on the {about} = {plane:g} plane, so its "
            f"mirror image is itself - use `place` for a part on the centre "
            f"line")
    return [(tuple(origin), base % 360),
            (tuple(far), _mirror_turn(base, about))]



# --------------------------------------------------------------------------
# `wall` and `box`
#
# The ops above all place one part, n times. These place *course-work*, and
# they are the only ops here that choose their own parts.
#
# The failure they answer is the one every other check in this project reports
# after the fact. A builder asked for a wall reaches for the brick it knows -
# 3001, the 2x4 - and lays it in rows: 3003 has been 26% of every part this
# agent places, and style.py flags one model in eight for being built out of a
# single size of part. Each of those rows is also a straight vertical joint
# running the full height of the wall, which is the one thing a real bricklayer
# never builds, because a wall whose seams line up is not bonded and comes
# apart on the seam.
#
# Both faults have the same cause and it is not ignorance. The builder knows
# what a bonded wall is. It is that laying one by hand means choosing a
# different brick for every course and offsetting each one by hand, which is
# forty numbers and thirty-nine chances to be a stud out - so it lays 2x4s in
# rows instead, and that is a rational response to the cost.
#
# So the cost goes away. `wall` takes a run and a height:
#
#     {"op": "wall", "colour": 4, "at": [0, 0, 0], "axis": "x",
#      "length_studs": 12, "courses": 3}
#
# and lays three bonded courses: longest brick that fits, seams offset course
# to course, no vertical joint anywhere in the wall. **The lengths are not an
# input and neither are the offsets** - same rule as the spacing in `row`, for
# the same reason. There is nowhere to type the wrong number.
#
# It also breaks the monotony for free, which is the part worth noticing. A
# bonded 12-stud course is 1x8 + 1x4; the course above it is 1x2 + 1x8 + 1x2.
# Three shapes rather than one, and ten parts rather than the twelve 2x4s the
# same wall costs laid in rows - without anybody having decided to vary
# anything. That is the distinction 20_pieces.md draws between variety earned
# and variety pursued: the shapes differ because the bond needed them to.
#
# It does *not* fix the size mix on its own, and it should not be read as
# doing so. A wall is structure and comes out structural and medium with no
# detail in it at all, which is correct - detail is what goes on the wall, and
# putting it there is a separate act. See style.SIZE_CLASS_SHARE.
#
# `box` is the same thing closed into a rectangle, which is the other half of
# the request: a cube, a room, a tower. Its corners alternate which pair of
# walls runs through, course by course, because that is what bonds a corner.

# Brick ladders, longest first. Hardcoded rather than searched: these are the
# parts 20_pieces.md already tells the builder it knows without a lookup, and a
# wall whose parts depend on how a search ranked that day is not reproducible.
# Verified against the catalogue - see _ladder, which refuses rather than
# guessing if one is ever retired.
LADDERS = {
    ("brick", 1): ((8, "3008"), (6, "3009"), (4, "3010"),
                   (3, "3622"), (2, "3004"), (1, "3005")),
    ("brick", 2): ((8, "3007"), (6, "2456"), (4, "3001"),
                   (3, "3002"), (2, "3003")),
    ("plate", 1): ((8, "3460"), (6, "3666"), (4, "3710"),
                   (3, "3623"), (2, "3023b"), (1, "3024")),
    ("plate", 2): ((8, "3034"), (6, "3795"), (4, "3020"),
                   (3, "3021"), (2, "3022")),
}
# Course rise, in LDU. A brick is 24 and a plate 8 - the two numbers the whole
# project counts in.
COURSE_RISE = {"brick": 24.0, "plate": 8.0}
# Leads tried when staggering a course, in order. The first that puts no seam
# above a seam wins. 0 is a course that starts flush, which is what every other
# course does.
LEADS = (0, 2, 3, 1, 4)


def _ladder(kind, thickness):
    """The (length, part_id) ladder for a wall, longest first."""
    if kind not in COURSE_RISE:
        raise BuildError(
            f"`kind` must be brick or plate, not {kind!r}")
    if thickness not in (1, 2):
        raise BuildError(
            f"`thickness_studs` must be 1 or 2, not {thickness} - a wall "
            f"thicker than two studs is two walls side by side")
    rungs = LADDERS[(kind, thickness)]
    for _, part_id in rungs:
        if catalog.get_part(part_id) is None:
            raise BuildError(
                f"`{part_id}` is not in the parts catalogue, so the {kind} "
                f"ladder cannot be used - build this wall with `row` instead")
    return rungs


def _fill(length, lead, sizes):
    """Partition a run of ``length`` studs, opening with a stub of ``lead``.

    Longest-first, which is what 20_pieces.md asks for - one 1x8 rather than
    two 1x4s, because every extra joint is a seam that shows and a coordinate
    that can be wrong. The one refinement is that a size which would strand a
    single stud is passed over: a 9-stud run is 6 + 3, never 8 + 1.
    """
    sizes = sorted({int(s) for s in sizes}, reverse=True)
    out, remaining = [], int(length)
    if lead:
        take = min(int(lead), remaining)
        if take:
            out.append(take)
            remaining -= take
    while remaining > 0:
        options = [s for s in sizes if s <= remaining]
        if not options:
            raise BuildError(
                f"a run of {remaining} stud(s) cannot be filled from the "
                f"available brick lengths {sizes}")
        out.append(next((s for s in options if remaining - s != 1), options[0]))
        remaining -= out[-1]
    return out


def _seams(run):
    """Interior joint positions of a filled course, in studs from its start."""
    seams, at = set(), 0
    for length in run[:-1]:
        at += length
        seams.add(at)
    return seams


def _bond(length, courses, sizes, forbid=()):
    """``courses`` filled runs whose seams never sit one above another.

    ``forbid`` are stud positions no seam may land on whatever the course
    below did. It exists for `box`: a run that happens to break exactly at the
    corner inset leaves the corner brick touching one wall and no other, and
    the box falls into two clumps over stud connections while every part of it
    still validates as touching. That is the hardest kind of fault to see in a
    render, and it is cheap to make impossible here.
    """
    forbid = set(forbid)
    out = []
    for index in range(courses):
        previous = (_seams(out[-1]) if out else set()) | forbid
        best = None
        for lead in LEADS:
            # A lead is a real brick, so it has to be a length the ladder
            # actually has. The 2-wide ladder stops at 2x2, and a lead of 1
            # there asks for a brick that was never moulded.
            if lead >= length or (lead and lead not in sizes):
                continue
            run = _fill(length, lead, sizes)
            shared = _seams(run) & previous
            if not shared:
                best = run
                break
            if best is None or len(shared) < len(_seams(best) & previous):
                best = run
        # A run of 1 or 2 studs has no interior seam to stagger, so the first
        # lead is always right and `best` is always set.
        out.append(best if best is not None else _fill(length, 0, sizes))
    return out


def _bond_wall(spans, sizes, forbid=()):
    """Bond one wall over its own courses, in absolute stud positions.

    ``spans`` is ``[(offset, length)]``, one per course - a box wall is the
    full side on the courses where it runs corner to corner and the inset
    stretch on the courses where the other pair does, so its offset and its
    length both change course to course.

    Bonding it in *local* coordinates, which is what bonding the two lengths
    separately amounts to, lines the seams up again: a 7-stud course breaking
    at stud 4 and the 5-stud course above it starting at stud 1 and breaking
    after 3 both break at stud 4. The wall is then two stacks side by side that
    happen to touch, and the box falls into four pieces with nothing overlapping
    and nothing off the grid - which is exactly the fault that is invisible in
    a render.
    """
    forbid = set(forbid)
    out, previous = [], set()
    for offset, length in spans:
        best = best_seams = None
        for lead in LEADS:
            if lead >= length or (lead and lead not in sizes):
                continue
            run = _fill(length, lead, sizes)
            seams = {offset + seam for seam in _seams(run)}
            shared = seams & (previous | forbid)
            if not shared:
                best, best_seams = run, seams
                break
            if best is None or len(shared) < len(best_seams & (previous | forbid)):
                best, best_seams = run, seams
        if best is None:
            best, best_seams = _fill(length, 0, sizes), set()
        out.append(best)
        previous = best_seams
    return out


def _course_placements(run, start, along, y, thickness, rungs):
    """``[(part_id, (x, y, z), matrix)]`` for one filled course.

    ``start`` is the (x, z) of the first *stud* of the course; ``along`` is the
    direction it runs. A part's origin sits at the centre of its own footprint,
    so a brick ``n`` studs long opening at that stud has its origin ``10 * (n -
    1)`` further along - the same arithmetic ``stud_offsets`` does, and the
    reason none of it is an input to the op.
    """
    by_length = {length: part_id for length, part_id in rungs}
    # Along z the brick is turned a quarter turn, so its length runs in z and
    # its thickness in x. The footprint turns with it; nothing else changes.
    matrix = _ROTATIONS[("y", 90 if along == "z" else 0)]
    thick_offset = 10.0 * (thickness - 1)

    out = []
    cursor = start[0] if along == "x" else start[1]
    for length in run:
        part_id = by_length.get(length)
        if part_id is None:
            raise BuildError(
                f"no {length}-stud part in the ladder - _fill returned a "
                f"length it was not given, which is a bug")
        long_offset = 10.0 * (length - 1)
        if along == "x":
            position = (cursor + long_offset, y, start[1] + thick_offset)
        else:
            position = (start[0] + thick_offset, y, cursor + long_offset)
        out.append((part_id, position, matrix))
        cursor += 20.0 * length
    return out


def _wall_placements(op):
    """Every part of a bonded wall, and the courses it laid."""
    origin = _at(op)
    axis = str(op.get("axis", "x")).lower()
    if axis not in ("x", "z"):
        raise BuildError(
            f"`wall.axis` must be x or z - the direction the wall runs - not "
            f"{axis!r}")
    kind = str(op.get("kind", "brick")).lower()
    try:
        thickness = int(op.get("thickness_studs", 1) or 1)
        length = int(op.get("length_studs") or 0)
        courses = int(op.get("courses", 1) or 1)
    except (TypeError, ValueError):
        raise BuildError(
            "`length_studs`, `courses` and `thickness_studs` must be whole "
            "numbers")
    rungs = _ladder(kind, thickness)

    if length < 1:
        raise BuildError(
            f"`length_studs` must be at least 1, not {length} - it is how many "
            f"studs the wall runs for")
    if courses < 1:
        raise BuildError(f"`courses` must be at least 1, not {courses}")

    sizes = [size for size, _ in rungs]
    if length < min(sizes):
        raise BuildError(
            f"a {length}-stud run cannot be laid {thickness} studs thick: the "
            f"shortest {thickness}-wide {kind} is {min(sizes)} studs long "
            f"(there is no {thickness} x {length}). Lay it with `place`, or "
            f"use thickness_studs 1.")

    rise = COURSE_RISE[kind]
    runs = _bond(length, courses, sizes)

    out = []
    for index, run in enumerate(runs):
        # -Y is up, so each course sits above the one before it.
        out += _course_placements(run, (origin[0], origin[2]), axis,
                                  origin[1] - rise * index, thickness, rungs)
    return out, runs


def _box_placements(op, ladder=None):
    """Four bonded walls, with the corners interlocked course by course.

    ``ladder`` is ``(rungs, thickness, rise)`` when the caller has already
    chosen the bricks - which is what `fill` does with the parts the builder
    named. Left out, the ladder comes from `kind` and `thickness_studs` exactly
    as it always has.
    """
    origin = _at(op)
    size = op.get("size_studs") or [op.get("width_studs"), op.get("depth_studs")]
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise BuildError(
            f"`{op['op']}.size_studs` must be [along_x, along_z] - the outside "
            "footprint in studs")
    kind = str(op.get("kind", "brick")).lower()
    try:
        width, depth = int(size[0]), int(size[1])
        courses = int(op.get("courses", 1) or 1)
        thickness = int(op.get("thickness_studs", 1) or 1)
    except (TypeError, ValueError):
        raise BuildError("`size_studs`, `courses` and `thickness_studs` must "
                         "be whole numbers")
    if ladder is None:
        rungs = _ladder(kind, thickness)
        rise = COURSE_RISE[kind]
    else:
        rungs, thickness, rise = ladder
    sizes = [s for s, _ in rungs]

    least = 2 * thickness + min(sizes)
    if width < least or depth < least:
        raise BuildError(
            f"a {width} x {depth} box with {thickness}-stud walls leaves an "
            f"inside stretch too short to fill - the shortest {thickness}-wide "
            f"{kind} is {min(sizes)} studs, so each side must be at least "
            f"{least}. Make it bigger, or lay a solid slab with `grid`.")
    # One course of a box is four walls that touch at the corners and are
    # joined to each other nowhere: it comes apart into four pieces the moment
    # it is picked up, and every part of it still validates as on-grid and
    # non-overlapping. The second course is what ties the corners, because the
    # pair that runs through alternates. This is the whole reason `box` exists
    # rather than four `wall` calls, so it refuses rather than laying the
    # version that looks right and is not.
    if courses < 2:
        raise BuildError(
            f"a box needs at least 2 courses, not {courses}. One course is "
            f"four separate walls: they meet at the corners without a single "
            f"stud between them, so the box falls apart into four pieces and "
            f"nothing in the geometry says so. The corners are tied by the "
            f"second course, where the other pair of walls runs through.")

    inset = 20.0 * thickness
    far_x = origin[0] + 20.0 * (width - thickness)
    far_z = origin[2] + 20.0 * (depth - thickness)

    # Which pair of walls runs the full span alternates course by course, and
    # that alternation is what bonds a corner. Laid the same way every course,
    # the four corners are four straight vertical joints - the fault `wall`
    # exists to prevent, rotated into the corners where it is harder to see.
    #
    # Each of the four walls is bonded over its own courses, in absolute stud
    # positions, because its length and its start both change with the
    # alternation. See _bond_wall.
    #
    # The through-courses may not break at the corner inset either: a brick
    # covering the corner and nothing else is held by the one wall it butts
    # against, and the box comes apart there over stud connections while every
    # part of it still reads as touching.
    x_spans = [(0, width) if i % 2 == 0 else (thickness, width - 2 * thickness)
               for i in range(courses)]
    z_spans = [(thickness, depth - 2 * thickness) if i % 2 == 0 else (0, depth)
               for i in range(courses)]
    x_runs = _bond_wall(x_spans, sizes, forbid=(thickness, width - thickness))
    z_runs = _bond_wall(z_spans, sizes, forbid=(thickness, depth - thickness))

    out = []
    for index in range(courses):
        y = origin[1] - rise * index
        x_offset, _ = x_spans[index]
        z_offset, _ = z_spans[index]
        x_start = origin[0] + 20.0 * x_offset
        z_start = origin[2] + 20.0 * z_offset

        out += _course_placements(x_runs[index], (x_start, origin[2]), "x", y,
                                  thickness, rungs)
        out += _course_placements(x_runs[index], (x_start, far_z), "x", y,
                                  thickness, rungs)
        out += _course_placements(z_runs[index], (origin[0], z_start), "z", y,
                                  thickness, rungs)
        out += _course_placements(z_runs[index], (far_x, z_start), "z", y,
                                  thickness, rungs)
    return out, {"width_studs": width, "depth_studs": depth,
                 "courses": courses, "thickness_studs": thickness}


# --------------------------------------------------------------------------
# `fill`
#
# `wall` and `box` bond course-work out of a ladder of bricks nobody chose, and
# across 1,938 recorded ops they were called three times between them - `wall`
# never once. The reading that fits the evidence is that taking no `part` is
# the reason rather than an incidental: a builder that has just been handed a
# palette and a design brief will not use the one op that ignores both.
#
# So `fill` is those two with the ladder handed over. It takes a region and the
# parts to tile it with, bonds them the same way - longest first, no seam above
# or beside another - and covers the case neither of the others does: a solid
# volume. A floor, a slab, a solid mass, a hollow room, a tower, all in one op
# with the arithmetic still nowhere near the builder.
#
#     {"op": "fill", "colour": 4, "at": [0, 0, 0], "size_studs": [10, 8],
#      "courses": 3, "parts": ["3001", "3004", "3010"]}
#
# Leave `parts` out and it falls back to the same ladder `wall` uses, so `fill`
# is a strict generalisation of both: `hollow: true` is `box`, one course of a
# 1-stud-deep region is `wall`.

def _ladder_from_parts(parts):
    """``(rungs, thickness, rise)`` from the parts a builder named.

    Every part has to be the same width and the same stacking height, because
    a course is one course: mixing a 1-wide with a 2-wide leaves a stripe of
    bare studs down the middle, and mixing a brick with a plate leaves a step.
    Both refuse here rather than being written and found in the render.
    """
    if isinstance(parts, str):
        parts = [parts]
    if not isinstance(parts, (list, tuple)) or not parts:
        raise BuildError(
            "`parts` must be a list of part ids to tile the region with - "
            "['3001', '3004'] - or leave it out to use the standard brick "
            "ladder")

    rungs, widths, rises, topless = [], set(), set(), []
    for name in parts:
        row, geometry = _geometry(name)
        part_id = row.get("part_id")
        if not geometry.get("has_top_studs"):
            topless.append(part_id)
        width = geometry.get("width_studs")
        depth = geometry.get("depth_studs")
        if not width or not depth:
            raise BuildError(
                f"`{part_id}` has no measured footprint, so it cannot tile a "
                f"region - place it with `place` or `row`")
        # The long side runs along the fill; the short side is the course's
        # thickness. The catalogue draws every rectangular part with its long
        # side on x, which is the orientation `_course_placements` turns from -
        # so a part measured the other way round is one this cannot lay without
        # guessing which way it was drawn.
        width, depth = int(width), int(depth)
        if depth > width:
            raise BuildError(
                f"`{part_id}` measures {width} x {depth} studs, longer across "
                f"than along, and `fill` lays its courses from the long side. "
                f"Place it with `row` and a `rotate`, where which way it faces "
                f"is yours to say.")
        length, thickness = width, depth
        rungs.append((length, part_id))
        widths.add(thickness)
        rises.add(round(float(_place_height(geometry, "y", 0)), 3))

    if len(widths) > 1:
        raise BuildError(
            f"the parts given are {sorted(widths)} studs wide and a course is "
            f"one width - a {min(widths)}-wide beside a {max(widths)}-wide "
            f"leaves a stripe of bare studs down the run. Fill in one width, "
            f"then fill the next stretch in the other.")
    if len(rises) > 1:
        raise BuildError(
            f"the parts given stack {sorted(rises)} LDU high and a course is "
            f"one height - mixing a brick with a plate leaves a step in the "
            f"middle of the course. Fill the brick courses and the plate "
            f"courses separately.")

    lengths = [length for length, _ in rungs]
    if len(set(lengths)) != len(lengths):
        raise BuildError(
            f"two of those parts are the same length ({sorted(lengths)}), so "
            f"one of them can never be chosen - give one part per length")
    rise = rises.pop()
    if rise <= 0:
        raise BuildError(
            f"those parts have no measured stacking height, so a second course "
            f"of them would land inside the first. Lay them with `row` or "
            f"`grid` on a course that is already there.")
    rungs.sort(reverse=True)
    return tuple(rungs), widths.pop(), rise, tuple(topless)


def _bond_area(length, rows, courses, sizes):
    """``(course, row) -> run``, with no seam above or beside another.

    `_bond` staggers a stack of courses and `_bond_wall` staggers one wall over
    its own courses. A solid fill is neither: it is a grid, and a seam in it has
    two neighbours rather than one - the run beside it in the same course and
    the run under it in the course below. A slab bonded only in one of those
    directions splits along the other, and every part of it validates.
    """
    grid = {}
    for course in range(courses):
        for row in range(rows):
            previous = set()
            if row:
                previous |= _seams(grid[(course, row - 1)])
            if course:
                previous |= _seams(grid[(course - 1, row)])
            best = None
            for lead in LEADS:
                if lead >= length or (lead and lead not in sizes):
                    continue
                run = _fill(length, lead, sizes)
                shared = _seams(run) & previous
                if not shared:
                    best = run
                    break
                if best is None or len(shared) < len(_seams(best) & previous):
                    best = run
            grid[(course, row)] = (best if best is not None
                                   else _fill(length, 0, sizes))
    return grid


def _fill_placements(op):
    """A region tiled with the parts the builder named, bonded."""
    origin = _at(op)
    size = op.get("size_studs") or [op.get("width_studs"), op.get("depth_studs")]
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise BuildError(
            "`fill.size_studs` must be [along_x, along_z] - the region to "
            "cover, in studs")
    try:
        width, depth = int(size[0]), int(size[1])
        courses = int(op.get("courses", 1) or 1)
    except (TypeError, ValueError):
        raise BuildError("`size_studs` and `courses` must be whole numbers")
    if width < 1 or depth < 1:
        raise BuildError(
            f"a {width} x {depth} region has nothing in it to fill")
    if courses < 1:
        raise BuildError(f"`courses` must be at least 1, not {courses}")

    axis = str(op.get("axis", "x")).lower()
    if axis not in ("x", "z"):
        raise BuildError(
            f"`fill.axis` must be x or z - the direction the courses run - "
            f"not {axis!r}")

    if op.get("parts"):
        rungs, thickness, rise, topless = _ladder_from_parts(op["parts"])
        # A part with no studs on its top is a part nothing can be laid on. One
        # course of them is a perfectly good surface - a field of tiles, a run
        # of slopes - and two courses is a second course resting on smooth
        # plastic, which no checker downstream can see because every part is on
        # the grid and none of them overlap.
        if topless and courses > 1:
            raise BuildError(
                f"`{topless[0]}` has no studs on top, so nothing can be built "
                f"on it - {courses} courses of it would be stacked on smooth "
                f"plastic. Fill one course of these, or fill the courses "
                f"underneath with bricks and lay these on the top.")
    else:
        kind = str(op.get("kind", "brick")).lower()
        thickness = int(op.get("thickness_studs", 1) or 1)
        rungs, rise = _ladder(kind, thickness), COURSE_RISE[kind]
    ladder = (rungs, thickness, rise)
    sizes = [s for s, _ in rungs]

    if _truthy(op.get("hollow")):
        # A shell is a box, and a box is what already knows how to interlock
        # four corners course by course.
        return _box_placements(dict(op, op="fill", size_studs=[width, depth],
                                    courses=courses), ladder=ladder)

    # A solid region. The runs go along `axis`; the rows step across it by the
    # thickness of the parts, so the cross measure has to be a whole number of
    # them.
    along, across = (width, depth) if axis == "x" else (depth, width)
    if across % thickness:
        raise BuildError(
            f"a {across}-stud stretch cannot be covered by {thickness}-stud-"
            f"wide parts - it is not a whole number of them. Make the region "
            f"{across - across % thickness} or {across + thickness - across % thickness} "
            f"studs across, or fill it with parts one stud wide.")
    if along < min(sizes):
        raise BuildError(
            f"a run of {along} stud(s) is shorter than the shortest part given "
            f"({min(sizes)} studs), so nothing fits along it. Use `row` or "
            f"`place` for a stretch this small.")

    rows = across // thickness
    if rows * courses * (along // min(sizes)) > MAX_PER_OP * 2:
        # A cheap upper bound before the bonding runs: a 40 x 40 x 10 slab of
        # 1x1s is not a fill, it is a file nobody can open.
        raise BuildError(
            f"a {width} x {depth} region {courses} course(s) high is far more "
            f"than the {MAX_PER_OP} parts one op may place. Fill it in "
            f"sections.")

    grid = _bond_area(along, rows, courses, sizes)

    out = []
    for course in range(courses):
        # -Y is up, so each course sits above the one before it.
        y = origin[1] - rise * course
        for row in range(rows):
            step = 20.0 * thickness * row
            if axis == "x":
                start = (origin[0], origin[2] + step)
            else:
                start = (origin[0] + step, origin[2])
            out += _course_placements(grid[(course, row)], start, axis, y,
                                      thickness, rungs)
    return out, {"width_studs": width, "depth_studs": depth,
                 "courses": courses, "thickness_studs": thickness,
                 "rows": rows, "axis": axis, "solid": True,
                 "parts_offered": [part_id for _, part_id in rungs]}


def _truthy(value):
    """A flag the model may have written as a string."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


# -- compiling ---------------------------------------------------------------

def _line(colour, position, matrix, part):
    def number(value):
        rounded = round(float(value), 3)
        return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"

    values = " ".join(number(v) for v in matrix)
    x, y, z = (number(v) for v in position)
    return f"1 {colour} {x} {y} {z} {values} {part}.dat"


def _snap_to_phase(op_origin, positions, part_id, matrix, target):
    """Move an op onto the lattice the model is already built on.

    A part's studs sit at a fixed offset from its origin that depends on the
    part, so two ops can both land on multiples of 20 and still be half a stud
    apart - see lattice.py. Until this existed the compiler laid ops down in
    whatever phase the `at` implied and the clash was only discovered
    afterwards, by the write gate, which then refused the whole call.

    It was the single largest way `build_ops` failed: of 263 misaligned parts
    across the recorded runs, 177 were off by exactly 10 LDU and another 38 by
    10*sqrt(2), which is the same half stud on a turned part.

    So the phase is corrected here instead, per op, before anything is written.
    Per op rather than per call is the point - the gate downstream could only
    fix a slip that was uniform across every op in the call, and a call that
    mixed a right op with a wrong one was rejected outright.

    Returns ``(positions, shift)``; shift is None when nothing moved.
    """
    from . import lattice

    here = lattice.phase(part_id, op_origin[0], op_origin[2], matrix)
    if here is None or target is None:
        return positions, None      # nothing the stud grid governs
    dx = lattice.correction(here[0], target[0])
    dz = lattice.correction(here[1], target[1])
    if not dx and not dz:
        return positions, None
    return ([(x + dx, y, z + dz) for x, y, z in positions],
            {"x": dx, "z": dz})


class _Leaf:
    """One op that places parts, and the motion it inherited from its groups.

    ``where`` names where in the source it came from - "op 3", or
    "op 3 > repeat 2/4 > op 1" - so a fault inside a group points at the op the
    builder actually wrote rather than at a position in a list nobody typed.
    """

    __slots__ = ("op", "transform", "where", "notes")

    def __init__(self, op, transform, where, notes=()):
        self.op = op
        self.transform = transform
        self.where = where
        self.notes = list(notes)


def _group_ops(op, where):
    """The child ops of a group, checked."""
    body = op.get("ops")
    if not isinstance(body, list) or not body:
        raise BuildError(
            f"{where}: `{op['op']}` needs an `ops` list - the ops it applies "
            f"to. It places nothing by itself.")
    return body


def _step_vector(op, where):
    """A `repeat`'s step, in LDU, checked against the lattice."""
    step = op.get("step") or op.get("by") or op.get("offset")
    if not isinstance(step, (list, tuple)) or len(step) != 3:
        raise BuildError(
            f"{where}: `repeat.step` must be [dx, dy, dz] in LDU - how far "
            f"each copy moves from the one before it")
    try:
        step = tuple(float(v) for v in step)
    except (TypeError, ValueError):
        raise BuildError(f"{where}: `repeat.step` must be three numbers")
    if not any(step):
        raise BuildError(
            f"{where}: `repeat.step` is [0, 0, 0], so every copy would land on "
            f"top of the last one. Give the direction the copies go in - "
            f"[0, -24, 0] is one brick course upward.")
    for index, axis in ((0, "x"), (2, "z")):
        if step[index] % LATTICE:
            raise BuildError(
                f"{where}: `repeat.step` moves {step[index]:g} LDU along {axis}, "
                f"which is not a multiple of {LATTICE:g} - every copy after "
                f"the first would land off the stud grid")
    return step


def _mirror_transform(op, where):
    """The reflection a `reflect` applies."""
    about = str(op.get("about", "x")).lower()
    if about not in ("x", "z"):
        raise BuildError(
            f"{where}: `reflect.about` must be x or z - the plane the group is "
            f"symmetric about - not {about!r}. Reflecting about y would turn "
            f"the build upside down; use `rotate` on the ops for that.")
    try:
        plane = float(op.get("plane", 0.0))
    except (TypeError, ValueError):
        raise BuildError(
            f"{where}: `reflect.plane` must be a number, not {op.get('plane')!r}")
    if (2 * plane) % LATTICE:
        raise BuildError(
            f"{where}: a mirror plane at {plane:g} LDU reflects the stud grid "
            f"off itself - every part in the copy would land off the grid. "
            f"Put the plane on a multiple of {LATTICE / 2:g}.")
    index = 0 if about == "x" else 2
    linear = list(_IDENTITY3)
    linear[index * 3 + index] = -1
    translate = [0.0, 0.0, 0.0]
    translate[index] = 2 * plane
    return _Transform(tuple(linear), tuple(translate), flipped=True)


def _call_transform(op, where):
    """Where a `call` puts the assembly it names, and which way round."""
    axis, degrees, matrix = _rotation(op.get("rotate"))
    if axis != "y" and degrees:
        raise BuildError(
            f"{where}: `call` turns the whole assembly about Y, so it cannot "
            f"be given a rotation about {axis} - that would lay the assembly "
            f"on its side and every part in it with it")
    transform = _Transform(matrix, tuple(_at(op)))
    if op.get("mirror"):
        about = str(op["mirror"]).lower()
        transform = transform.then(
            _mirror_transform({"op": "call", "about": about,
                               "plane": op.get("plane", 0.0)}, where))
    return transform


def _expand(ops, transform=IDENTITY, where="", defined=None, depth=0,
            out=None, expanding=()):
    """Flatten a tree of ops into the leaves that place parts.

    Every group op resolves to its children carrying a motion; nothing else
    changes. The leaves that come out of here go through exactly the same
    geometry, phase snapping and grid check as an op written flat, which is the
    property the whole tool rests on - the compiler still checks every
    placement it writes.
    """
    if out is None:
        out = []
    if defined is None:
        defined = {}
    if depth > MAX_GROUP_DEPTH:
        raise BuildError(
            f"{where}: groups are nested more than {MAX_GROUP_DEPTH} deep. "
            f"That is almost always a `call` that reaches itself; build the "
            f"inner assembly with its own build_ops call instead.")

    for number, op in enumerate(ops, start=1):
        if not isinstance(op, dict):
            raise BuildError(f"{where or 'ops'}: op {number} is not an object: {op!r}")
        kind = str(op.get("op") or "").strip().lower()
        here = f"{where} > op {number}" if where else f"op {number}"
        if kind not in OPS:
            raise BuildError(
                f"{here}: unknown op `{op.get('op')}` - the ops are "
                f"{', '.join(OPS)}")

        if kind not in GROUP_OPS:
            if len(out) >= MAX_EXPANDED_OPS:
                raise BuildError(
                    f"{here}: these ops expand to more than "
                    f"{MAX_EXPANDED_OPS} operations. Check the `times` on the "
                    f"repeats - build it in sections rather than in one call.")
            note = str(op.get("note") or "").strip()
            out.append(_Leaf(op, transform, here, [note] if note else []))
            continue

        note = str(op.get("note") or "").strip()
        mark = len(out)

        if kind == "define":
            if depth:
                raise BuildError(
                    f"{here}: `define` belongs at the top of the call, not "
                    f"inside another op. A definition inside a `repeat` would "
                    f"be made afresh on every copy; write it once above and "
                    f"`call` it from in there.")
            name = str(op.get("name") or "").strip()
            if not name:
                raise BuildError(
                    f"{here}: `define` needs a `name` - what to call this "
                    f"assembly, so a `call` can ask for it")
            if name in defined:
                raise BuildError(
                    f"{here}: `{name}` is already defined. Names are used "
                    f"once; a second definition would leave the calls above it "
                    f"and the calls below it meaning different things.")
            # Held as written, relative to [0, 0, 0]. Nothing is placed: a
            # definition is a shape the build can ask for, not part of it.
            defined[name] = _group_ops(op, here)
            continue

        if kind == "call":
            name = str(op.get("name") or "").strip()
            if name not in defined:
                known = ", ".join(sorted(defined)) or "nothing yet"
                raise BuildError(
                    f"{here}: nothing called `{name}` has been defined "
                    f"(defined so far: {known}). `define` it earlier in this "
                    f"same call - definitions do not carry from one build_ops "
                    f"to the next.")
            if name in expanding:
                raise BuildError(
                    f"{here}: `{name}` calls itself, which never finishes")
            inner = _call_transform(op, here).then(transform)
            _expand(defined[name], inner, f"{here} ({name})", defined,
                    depth + 1, out, expanding + (name,))
        elif kind == "repeat":
            times = _count(op, "times")
            step = _step_vector(op, here)
            body = _group_ops(op, here)
            for index in range(times):
                moved = _Transform(
                    _IDENTITY3, tuple(v * index for v in step)).then(transform)
                _expand(body, moved, f"{here} (copy {index + 1}/{times})",
                        defined, depth + 1, out, expanding)
        else:                                       # reflect
            body = _group_ops(op, here)
            mirrored = _mirror_transform(op, here).then(transform)
            # The original stays unless the caller only wants the far side -
            # "and the same on the other side" is what this op is for, and a
            # reflect that swallowed its own input would be a surprise.
            if op.get("keep", True):
                _expand(body, transform, f"{here} (near side)", defined,
                        depth + 1, out, expanding)
            _expand(body, mirrored, f"{here} (far side)", defined,
                    depth + 1, out, expanding)

        # The group's own note goes above the first thing it laid, so the file
        # reads as "the four courses" rather than as forty unexplained lines.
        if note and len(out) > mark:
            out[mark].notes.insert(0, note)

    return out


def compile_ops(ops, phase=None):
    """Turn a list of ops into LDraw lines. Returns ``(lines, report)``.

    Raises ``BuildError`` naming the op and what is wrong with it - the ops are
    a program, and a program that will not compile should say which statement
    failed rather than producing a model that is subtly wrong.

    ``phase`` is the stud lattice the destination model already stands on, as
    ``(phase_x, phase_z)``. Given one, every op is laid down on it rather than
    on whatever its own `at` implied. See `_snap_to_phase`.
    """
    if isinstance(ops, str):
        try:
            ops = json.loads(ops)
        except ValueError as exc:
            raise BuildError(f"`ops` is not valid JSON: {exc}")
    if isinstance(ops, dict):
        ops = ops.get("ops") or [ops]
    if not isinstance(ops, list) or not ops:
        raise BuildError("`ops` must be a non-empty list of operations")

    # Groups first: `repeat`, `reflect`, `define` and `call` place nothing and
    # are resolved into the leaves they contain, each carrying the motion its
    # groups gave it. A flat list of ops comes back through this unchanged.
    leaves = _expand(ops)
    if not leaves:
        raise BuildError(
            "these ops place nothing - a `define` on its own defines an "
            "assembly and does not build it. Add the `call` that puts it "
            "somewhere.")

    lines, placed, steps = [], 0, []

    for leaf in leaves:
        op, moved, number = leaf.op, leaf.transform, leaf.where
        kind = str(op.get("op") or "").strip().lower()
        op = dict(op, op=kind)

        snapped = None
        masonry = None
        try:
            colour = _colour(op)

            # `wall`, `box` and `fill` choose their own parts from a ladder, so
            # they cannot go through `_geometry`, which resolves the single
            # `part` every other op names. They come back as finished
            # placements instead.
            if kind in ("wall", "box", "fill"):
                if op.get("part"):
                    raise BuildError(
                        f"`{kind}` lays a ladder of bricks chosen to bond, so "
                        f"it takes no `part`."
                        + (" Give it `parts` - the list to tile the region "
                           "with - or leave that out for the standard brick "
                           "ladder." if kind == "fill" else
                           " Use `fill` with `parts` to say which bricks, or "
                           "`row` to lay one named part repeatedly."))
                if op.get("rotate"):
                    raise BuildError(
                        f"`{kind}` turns its own bricks; give it `axis` to say "
                        f"which way the courses run, not `rotate`")
                placements, masonry = (
                    _wall_placements(op) if kind == "wall" else
                    _fill_placements(op) if kind == "fill" else
                    _box_placements(op))
                if not placements:
                    raise BuildError(f"`{kind}` laid nothing")
                if len(placements) > MAX_PER_OP:
                    raise BuildError(
                        f"this {kind} is {len(placements)} parts; one op may "
                        f"place at most {MAX_PER_OP}. Build it in sections.")
                parts = [part_id for part_id, _, _ in placements]
                positions = [moved.point(point) for _, point, _ in placements]
                matrices = [moved.matrix(turn) for _, _, turn in placements]
                geometry = {"part_id": parts[0]}
                row, extents, degrees, axis = None, None, 0, "y"
                positions, snapped = _snap_to_phase(
                    positions[0], positions, parts[0], matrices[0], phase)
                raise _Laid

            row, geometry = _geometry(op.get("part"))
            geometry = dict(geometry, part_id=row.get("part_id"))
            axis, degrees, matrix = _rotation(op.get("rotate"))
            extents = _extents(geometry, matrix)
            if kind == "stack":
                op["_height"] = _place_height(geometry, axis, degrees)

            if kind in ("ring", "mirror"):
                # Each copy carries its own turn, so position and matrix travel
                # together rather than one matrix answering for the whole op.
                if axis != "y" and degrees:
                    raise BuildError(
                        f"`{kind}` turns each copy about Y, so it cannot also "
                        f"be given a rotation about {axis} - place those parts "
                        f"individually")
                op["_degrees"] = degrees
                turned = _turned_positions(op, extents, geometry)
                positions = [point for point, _ in turned]
                matrices = [_ROTATIONS[("y", turn)] for _, turn in turned]
            else:
                positions = _positions(op, extents, geometry)
                matrices = [matrix] * len(positions)

            # The motion this op inherited from the groups around it, applied
            # once the op's own geometry has been worked out. Position and
            # rotation move together; the anchor the phase is read from moves
            # with them, so an op inside a group is snapped exactly as the same
            # op written flat would be.
            anchor = moved.point(_at(op))
            if not moved.identity:
                positions = [moved.point(p) for p in positions]
                matrices = [moved.matrix(m) for m in matrices]
            parts = [geometry["part_id"]] * len(positions)
            positions, snapped = _snap_to_phase(
                anchor, positions, geometry["part_id"],
                moved.matrix(matrix), phase)
        except _Laid:
            pass                    # a wall, a box or a fill, placed above
        except BuildError as exc:
            raise BuildError(f"{number} ({kind} {op.get('part') or ''}".rstrip()
                             + f"): {exc}")

        off_grid = [p for p in positions
                    if abs(p[0] % LATTICE) > 0.01 or abs(p[2] % LATTICE) > 0.01]
        if off_grid:
            raise BuildError(
                f"{number} ({kind} {geometry['part_id']}): "
                f"x = {off_grid[0][0]:g}, z = {off_grid[0][2]:g} is not on the "
                f"stud grid - x and z must be multiples of {LATTICE:g} LDU")

        placed += len(positions)
        if placed > MAX_TOTAL:
            raise BuildError(
                f"these ops place {placed} parts, over the limit of "
                f"{MAX_TOTAL} for one call")

        for note in leaf.notes:
            lines.append(f"0 // {note}")
        for part_id, position, turn in zip(parts, positions, matrices):
            lines.append(_line(colour, position, turn, part_id))

        if masonry is not None:
            used = {}
            for part_id in parts:
                used[part_id] = used.get(part_id, 0) + 1
            steps.append({
                "op": kind,
                "parts_placed": len(positions),
                "bricks_used": used,
                "colour": colour,
                "bonded": ("no vertical joint runs through two courses - the "
                           "lengths and the offsets were chosen to break every "
                           "seam"),
                **({"courses_laid": [list(run) for run in masonry]}
                   if kind == "wall" else masonry),
            })
            if snapped:
                steps[-1]["snapped_to_lattice"] = {
                    **snapped,
                    "why": ("moved onto the stud lattice the model already "
                            "stands on - its studs line up now."),
                }
            continue

        steps.append({
            "op": kind,
            "part": geometry["part_id"],
            "description": row.get("description"),
            "colour": colour,
            "parts_placed": len(positions),
            "footprint_ldu": {"x": extents[0], "z": extents[2]},
            "rotation": f"{degrees}° about {axis}" if degrees else "none",
            "from": list(positions[0]),
            "to": list(positions[-1]),
        })
        if snapped:
            steps[-1]["snapped_to_lattice"] = {
                **snapped,
                "why": ("moved onto the stud lattice the model already stands "
                        "on - its studs line up now. Take this offset into "
                        "account for the next op rather than repeating it."),
            }
        if kind in ("row", "grid") and len(positions) > 1:
            steps[-1]["pitch_ldu"] = {
                "x": _pitch(op, extents, 0, geometry),
                "z": _pitch(op, extents, 2, geometry),
            }
        if kind == "stack" and len(positions) > 1:
            steps[-1]["rise_ldu"] = op["_height"]
        if " > " in number or "(" in number:
            # It came out of a group, so say which one: "op 2 (copy 3/4) > op 1"
            # is the difference between reading the report as the program that
            # was written and reading it as a list nobody typed.
            steps[-1]["from_group"] = number

    report = {"ops": len(leaves), "parts_placed": placed, "steps": steps}
    if len(leaves) != len(ops):
        # A grouped call: say what the groups came to, so the builder can see
        # that four courses really were laid and does not write them out again
        # by hand to be sure.
        report["ops_written"] = len(ops)
        report["expanded_to"] = len(leaves)
    return lines, report
