#!/usr/bin/env python3
"""
ldr_connectivity_checker.py

Checks whether an LDraw model is actually *buildable* out of real bricks, by
testing stud-to-anti-stud alignment rather than bounding-box overlap.

WHY THIS EXISTS
---------------
A collision check (see ldr_collision_checker.py) cannot tell a correct build
from a broken one. Two bricks that are properly connected overlap by ~4 LDU
(the stud sits inside the anti-stud), and a bracket's bounding box legitimately
encloses space another brick occupies. Meanwhile a plate resting *between*
studs - physically impossible - also overlaps by ~4 LDU. The signals are
identical.

What separates them is the stud grid: a System part attaches only where a stud
meets a matching anti-stud. This tool finds those points and checks them.

HOW CONNECTION POINTS ARE DERIVED
---------------------------------
Studs (male) are found by recursively expanding each part and recording every
reference to a "stud*" primitive. LDraw authors a stud with its base at y=0
extending to y=-4 (upward, since -Y is up), so the reference point sits exactly
on the part's top surface. An underside tube is the same primitive flipped
(e.g. 3003 uses "stud4.dat" with a -5 Y scale), so a stud's *transformed axis*
tells male from female without name-matching guesswork.

Anti-studs (female) are NOT read from the tube geometry: a 2x2 brick has one
central tube at (0,0) that grips four studs at (+-10,+-10), so tube position is
not receiving position. Instead the receiving grid is the part's own stud grid
projected onto its bottom face - a 2x2 brick has studs at (+-10,+-10) and
receives at (+-10,+-10), which holds for bricks, plates and their relatives.
Parts with no studs at all (tiles, slopes, cheese wedges) fall back to a 20 LDU
lattice inferred from their footprint.

A connection exists where a male point and a female point coincide within
--tolerance and their axes are opposed.

ROUND ELEMENTS IN A 2x2 CELL
----------------------------
One legal connection is not a coincidence of points at all. A 1x1 round brick
or plate is a whole stud across with a cylindrical wall, so it can be dropped
into the middle of four studs, where the four stud walls grip the barrel. The
same element attaches the other way up, its own stud entering the tube at the
centre of the four receiving positions above it.

Both leave the part exactly half a stud off the grid in x and z, which is the
signature of a broken build everywhere else - a flat 14.14 LDU (10 * sqrt 2)
from four points at once. It is checked separately, after the stud grid has
had its say, and only for parts whose wall really is round at stud radius;
a 2x2 round brick offset the same way grips nothing and still fails.

WHAT IT REPORTS
---------------
  CONNECTED    - part has at least one valid stud connection.
  MISALIGNED   - no valid connection, but a mating point of the opposite kind
                 sits within one stud pitch. The part is *nearly* on the grid
                 and off by a non-grid amount. This is the smoking gun for a
                 broken build.
  UNVERIFIED   - no connection and nothing nearby. Either genuinely floating,
                 or held by a joint this tool does not model (clips, bars,
                 Technic pins, hinges, brackets, minifig fittings).

LIMITATION: only stud/anti-stud System connections are understood. A model
full of clips and Technic pins will report many UNVERIFIED parts that are
perfectly fine. MISALIGNED is the signal to act on; compare UNVERIFIED counts
against a known-good baseline.

EXIT CODE
---------
    0  - no misaligned parts
    1  - misaligned parts found
    2  - fatal error
"""

import argparse
import json
import math
import os
import sys

# Imported two ways, and both have to work: as a package member
# (`maister.environment_feedback.ldr_connectivity_checker`), and as a top-level
# module - which is the path every build actually takes, because
# maister/agent/validation.py puts this directory on sys.path and imports the
# checkers bare.
try:
    from .ldr_collision_checker import (
        norm_name, parse_ldr_file, flatten_model, unflattened,
        mat_vec_mul, mat_mul, compute_part_bbox, get_part_lines, IDENTITY,
    )
except ImportError:
    from ldr_collision_checker import (
        norm_name, parse_ldr_file, flatten_model, unflattened,
        mat_vec_mul, mat_mul, compute_part_bbox, get_part_lines, IDENTITY,
    )

STUD_PITCH = 20.0
# Half a pitch: the centre of a 2x2 cell of studs, where a round element sits.
CRADLE_OFFSET = STUD_PITCH / 2.0


# --------------------------------------------------------------------------
# Stud extraction
# --------------------------------------------------------------------------

def _is_stud_primitive(name):
    """
    True for a leaf stud primitive. "stug*" files are stud *groups* (e.g.
    stug-2x2.dat holds four stud.dat references) - those must be recursed
    into, not treated as a single stud.
    """
    base = norm_name(name).split("/")[-1]
    return base.startswith("stud") and not base.startswith("stug")


def _normalize(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


# --------------------------------------------------------------------------
# Round elements
#
# A 1x1 round brick or plate is 20 LDU across - a whole stud - and its wall is
# a cylinder rather than a box. That is what lets it clutch at the centre of a
# 2x2 cell of studs: the four stud walls grip the barrel. The same element
# seats under a plate the other way up, its stud entering the tube at the
# centre of that cell. Both are legal, both put the part half a stud off the
# grid in x and z, and neither is a stud-to-anti-stud coincidence, so they are
# recognised separately (see the cradle pass in build_graph).
# --------------------------------------------------------------------------

# Circular primitives. A part whose outer wall is one of these at full stud
# radius is round; the tubes and pin holes inside a square brick use the same
# primitives at a smaller radius, which is why the radius is what decides.
_ROUND_PRIMITIVES = ("4-4cyl", "4-4disc", "4-4ndis", "4-4con", "4-4chr",
                     "4-4ring", "4-4edge")
ROUND_RADIUS = STUD_PITCH / 2.0     # 10 LDU: one stud across
ROUND_TOLERANCE = 1.0


def _has_round_wall(part_name, library_root, model, stack, mat=IDENTITY,
                    pos=(0.0, 0.0, 0.0), depth=0, radius=ROUND_RADIUS,
                    tolerance=ROUND_TOLERANCE):
    """A circular primitive of ``radius``, centred on the part's own axis.

    ``radius`` is the wall to insist on: ``ROUND_RADIUS`` for the 1x1 element
    that clutches between four studs, or the part's own half-width for the
    round *body* that seats on a single stud. See ``part_has_central_tube``.
    """
    key = norm_name(part_name)
    base = key.split("/")[-1]

    if base.startswith(_ROUND_PRIMITIVES):
        # The primitives are authored at unit radius, so the transformed x and
        # z basis vectors are the radii this reference was scaled to.
        rx = math.hypot(mat[0], mat[6])
        rz = math.hypot(mat[2], mat[8])
        if abs(rx - rz) > ROUND_TOLERANCE:
            return False           # an ellipse is not a tube
        if abs(pos[0]) > ROUND_TOLERANCE or abs(pos[2]) > ROUND_TOLERANCE:
            return False           # off the part's own axis
        return abs(rx - radius) <= tolerance

    # part -> subpart -> primitive is as deep as this needs to look
    if depth >= 3 or key in stack:
        return False
    lines = get_part_lines(part_name, library_root, model)
    if lines is None:
        return False

    stack = stack | {key}
    for line in lines:
        tokens = line.strip().split()
        if len(tokens) < 15 or tokens[0] != "1":
            continue
        try:
            t = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
            m = [float(v) for v in tokens[5:14]]
        except ValueError:
            continue
        child = " ".join(tokens[14:]).strip()
        wt = mat_vec_mul(mat, t)
        if _has_round_wall(child, library_root, model, stack, mat_mul(mat, m),
                           (wt[0] + pos[0], wt[1] + pos[1], wt[2] + pos[2]),
                           depth + 1, radius, tolerance):
            return True
    return False


# Round bodies wider than one stud, which seat on a single central stud.
#
# A 2x2 round brick does not have four anti-studs underneath. It has ONE
# circular tube, and that tube fits over a single stud at its centre exactly as
# well as it fits down over four. Real sets use both constantly: a 2x2 round
# brick on a lamppost, a round plate on a 1x1, a dish capping a single stud.
#
# The checker only ever offered such a part its four footprint positions, so a
# round brick on a lamppost measured 14.14 LDU (10 * sqrt 2, the diagonal) from
# every one of them and was reported off the grid. That one arrangement was the
# largest single class of false alarm left in the reference corpus.
#
# Radius is not fixed here, unlike `part_is_round` above: that one is asking
# "is this the 1x1 element that clutches *between* four studs", which is only
# true at stud radius. This is asking "does this part have a central tube",
# which is true of a 2x2 round, a 4x4 round and a dish alike.
# How far the wall radius may be from half the part's width and still be the
# part's outer wall. Looser than ROUND_TOLERANCE because a moulded wall is
# drawn a little inside its nominal envelope.
_ROUND_BODY_TOLERANCE = 2.5
_round_body_cache = {}

# Parts the caller knows are round-bodied, by normalised name.
#
# The geometric test below reads the part's own wall, and for most round parts
# that is enough. It is not enough for all of them: 4032a and 60474 draw their
# rims as explicit triangles rather than as a cylinder primitive, so there is
# no radius to measure and they come back square. They are still round plates.
#
# Rather than loosen the geometry - which is what keeps a 2x2 *square* plate
# out, and that exclusion is load-bearing - the caller may name them. The
# checker stays a piece of geometry that knows nothing about the catalogue;
# whoever has a catalogue fills this in. See maister/agent/validation.py.
CENTRAL_TUBE_PARTS = set()


def part_has_central_tube(part_name, library_root, model, bbox_cache):
    """True for a part whose *outer wall* is a cylinder, wider than one stud.

    Such a part seats on a single stud at its own centre, so that centre is a
    receiving position - see part_connection_points.

    The radius has to match the part's own half-width, and that is the whole
    of the test's precision. Every 2x2 plate and brick in the catalogue has a
    circular anti-stud tube inside it, so "contains a cylinder centred on the
    axis" is true of practically everything square and would hand a centre
    seat to parts that have no business with one - which would then accept a
    half-stud diagonal offset on any 2x2, and that offset is a real fault.
    A cylinder as wide as the part *is* the part: that is a round brick.
    """
    key = norm_name(part_name)
    if key in CENTRAL_TUBE_PARTS or key.removesuffix(".dat") in CENTRAL_TUBE_PARTS:
        return True
    if key in _round_body_cache:
        return _round_body_cache[key]

    result = False
    bbox = compute_part_bbox(part_name, library_root, bbox_cache, model=model)
    if bbox is not None:
        (minx, _, minz), (maxx, _, maxz) = bbox
        span_x, span_z = maxx - minx, maxz - minz
        # Square, and bigger than the 1x1 the cradle pass already handles.
        if (abs(span_x - span_z) <= ROUND_TOLERANCE
                and span_x > STUD_PITCH + ROUND_TOLERANCE):
            result = _has_round_wall(part_name, library_root, model, set(),
                                     radius=span_x / 2.0,
                                     tolerance=_ROUND_BODY_TOLERANCE)

    _round_body_cache[key] = result
    return result


def part_is_round(part_name, library_root, cache, bbox_cache, model):
    """True for a one-stud round element: 20x20 footprint and a round wall.

    The footprint test is what keeps this to the elements the connection
    actually works for. A 2x2 round brick is round too, but offset diagonally
    it grips nothing, so it must keep failing.
    """
    key = norm_name(part_name)
    if key in cache:
        return cache[key]

    result = False
    bbox = compute_part_bbox(part_name, library_root, bbox_cache, model=model)
    if bbox is not None:
        (minx, _, minz), (maxx, _, maxz) = bbox
        if (abs((maxx - minx) - STUD_PITCH) <= ROUND_TOLERANCE
                and abs((maxz - minz) - STUD_PITCH) <= ROUND_TOLERANCE):
            result = _has_round_wall(part_name, library_root, model, set())

    cache[key] = result
    return result


def part_studs(part_name, library_root, cache, model, stack=None):
    """
    Recursively collect stud primitives of a part, in the part's local frame.
    Returns a list of (position, axis) where axis is the outward direction the
    stud points (a stud is authored pointing -Y, i.e. up).
    """
    key = norm_name(part_name)

    if _is_stud_primitive(key):
        # authored with base at y=0, body extending to y=-4 (upward)
        return [((0.0, 0.0, 0.0), (0.0, -1.0, 0.0))]

    if key in cache:
        return cache[key]
    if stack is None:
        stack = set()
    if key in stack:
        return []
    stack.add(key)

    lines = get_part_lines(part_name, library_root, model)
    if lines is None:
        cache[key] = []
        stack.discard(key)
        return []

    studs = []
    for line in lines:
        tokens = line.strip().split()
        if len(tokens) < 15 or tokens[0] != "1":
            continue
        try:
            t = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
            m = [float(v) for v in tokens[5:14]]
        except ValueError:
            continue
        child = " ".join(tokens[14:]).strip()
        for (p, a) in part_studs(child, library_root, cache, model, stack):
            wp = mat_vec_mul(m, p)
            studs.append(((wp[0] + t[0], wp[1] + t[1], wp[2] + t[2]),
                          _normalize(mat_vec_mul(m, a))))

    stack.discard(key)
    cache[key] = studs
    return studs


# How far a bounding box may be off a whole number of studs and still be read
# as a footprint. Curved slopes overhang their own stud grid by a few tenths;
# anything past this is not overhang, it is a part with something sticking out.
FOOTPRINT_TOLERANCE = 2.0


def footprint_lattice(bbox):
    """
    20 LDU stud lattice inferred from a part's footprint, or [] if it has none.

    Snapped to the nearest 10 LDU: stud centres in a part's local frame sit on
    a 10 LDU lattice, but a raw bounding box can be a few tenths wider than the
    nominal footprint (curved slopes like 11477 overhang their stud grid), which
    would otherwise shift every inferred position off the grid.

    **A bounding box only describes a footprint when it measures a whole number
    of studs.** That sounds like a technicality and it is the difference between
    a check that works and one that does not. A bounding box is the whole part,
    protrusions included: 60897 is a 1x1 plate with a clip on it and measures
    20 x 34, so rounding gave it a 1 x 2 footprint and put two receiving
    positions 10 LDU either side of the one place it can actually be seated.
    Every part carrying a clip, a bar, a tap spout, a hinge stick or a bracket
    upstand was being told it was half a stud off the grid - in models that
    came in a box.

    So a box that is not a whole number of studs across is not measured. The
    part's own studs still describe where it seats (see part_connection_points),
    and where it has none it goes unverified - which is the honest answer for a
    shape this cannot read, and far better than a confident wrong one.
    """
    (minx, _, minz), (maxx, _, maxz) = bbox
    span_x, span_z = maxx - minx, maxz - minz

    def studs(span):
        """Whole studs across, or None if this span is not a footprint."""
        n = max(1, int(round(span / STUD_PITCH)))
        return n if abs(span - n * STUD_PITCH) <= FOOTPRINT_TOLERANCE else None

    nx, nz = studs(span_x), studs(span_z)
    if nx is None or nz is None:
        return []

    snap = lambda v: round(v / 10.0) * 10.0
    return [(snap(minx + STUD_PITCH * (i + 0.5)), snap(minz + STUD_PITCH * (j + 0.5)))
            for i in range(nx) for j in range(nz)]


def tube_lattice(bbox):
    """Where a part's underside grips a *single* stud - the jumper positions.

    A part does not only seat on the grid of studs its own footprint covers. It
    also grips one stud held between them: that is what the tubes and ribs
    under a plate are, and it is exactly what a jumper plate exists to offer -
    one stud at the centre of a 1x2, which a 1x2 tile then sits on.

    Tiles were the largest group of complaints left on the sound test, and a
    tile on a jumper measures 10 LDU off every footprint cell it has, which is
    the signature this answers.

    The positions are the midpoints between adjacent cells: for a 1xN the N-1
    points along the row, for a WxD the (W-1)x(D-1) interior vertices where a
    2x2's single tube sits. **A 1x1 has no adjacent cells and so gets nothing**,
    which is what keeps "a 1x1 plate standing between four studs" a fault - it
    has no tube to grip one with.

    This is safe in a way it first looks like it should not be. A seat only
    ever mates if a stud is *physically at that point*, so this does not accept
    a half-stud offset in general: it accepts one exactly where a real stud is
    waiting, which is the definition of a jumper connection.
    """
    (minx, _, minz), (maxx, _, maxz) = bbox
    cells = footprint_lattice(bbox)
    if not cells:
        return []

    xs = sorted({x for x, _ in cells})
    zs = sorted({z for _, z in cells})

    def between(values):
        """Midpoints between adjacent cells, or the single cell if there is one."""
        if len(values) < 2:
            return values
        return [(a + b) / 2.0 for a, b in zip(values, values[1:])]

    return [(x, z) for x in between(xs) for z in between(zs)]


def is_axis_aligned(m, eps=1e-6):
    """
    True if the 3x3 is a signed permutation matrix - the part is placed at a
    multiple of 90 degrees. A part rotated to an arbitrary angle was placed
    off-grid deliberately (hinge, clip, decorative tilt), so a near-miss to the
    stud grid says nothing about it.
    """
    for r in range(3):
        row = m[r * 3:r * 3 + 3]
        nonzero = [v for v in row if abs(v) > eps]
        if len(nonzero) != 1 or abs(abs(nonzero[0]) - 1.0) > 1e-4:
            return False
    for c in range(3):
        col = [m[c], m[c + 3], m[c + 6]]
        if len([v for v in col if abs(v) > eps]) != 1:
            return False
    return True


def part_connection_points(part_name, library_root, stud_cache, bbox_cache, model):
    """
    Returns (males, females) in the part's local frame, each a list of
    (position, axis).
    """
    studs = part_studs(part_name, library_root, stud_cache, model)
    bbox = compute_part_bbox(part_name, library_root, bbox_cache, model=model)

    # Every stud primitive except the ones facing down: those are the underside
    # tubes, authored as a flipped stud (a 6x6 plate has 36 studs and 25 tubes,
    # and counting the tubes as studs is 25 points that can never mate).
    # Sideways studs on brackets and SNOT parts have no Y component at all and
    # stay in.
    males = [(p, a) for (p, a) in studs if a[1] < 0.9]
    if bbox is None:
        return males, []

    bottom = bbox[1][1]  # +Y is down, so the bottom face is max_y

    # Receiving grid = this part's own stud grid, projected to the bottom face.
    # Only studs pointing along local -Y define the grid (sideways studs on
    # brackets/SNOT parts do not describe where the part can be seated).
    grid = {(round(p[0], 3), round(p[2], 3))
            for (p, a) in studs if a[1] < -0.9}

    # Union with the footprint lattice, where the box is a footprint at all.
    # For a plain brick or plate the two agree and nothing changes; for a
    # jumper (15573: one stud at the centre but seats on the two normal 1x2
    # positions) the lattice supplies the receiving points the stud grid alone
    # would miss. For a part whose box is bigger than its footprint - a clip, a
    # bracket upstand, a tap spout - `footprint_lattice` now declines to guess
    # and answers with nothing, leaving the part's own studs to say where it
    # seats. See its docstring.
    grid |= set(footprint_lattice(bbox))

    # ...and where its tubes grip one stud held between those cells, which is
    # what a jumper plate offers. See tube_lattice.
    grid |= set(tube_lattice(bbox))

    # A round body wider than one stud has a single central tube, and that tube
    # goes over one stud as readily as over four - a 2x2 round brick on a
    # lamppost, a dish capping a 1x1. Its centre is therefore a real receiving
    # position and is not among the footprint cells, which sit at +-10 around
    # it. See part_has_central_tube.
    if part_has_central_tube(part_name, library_root, model, bbox_cache):
        (minx, _, minz), (maxx, _, maxz) = bbox
        snap = lambda v: round(v / 10.0) * 10.0          # noqa: E731
        grid.add((snap((minx + maxx) / 2.0), snap((minz + maxz) / 2.0)))

    females = [((x, bottom, z), (0.0, 1.0, 0.0)) for (x, z) in sorted(grid)]
    return males, females


# --------------------------------------------------------------------------
# World-space connection graph
# --------------------------------------------------------------------------

def to_world(inst, points):
    out = []
    for (p, a) in points:
        wp = mat_vec_mul(inst.matrix, p)
        out.append(((wp[0] + inst.pos[0], wp[1] + inst.pos[1], wp[2] + inst.pos[2]),
                    _normalize(mat_vec_mul(inst.matrix, a))))
    return out


def _hash_points(points, cell):
    """Spatial hash of (point, axis, index) triples, one bucket per cell."""
    grid = {}
    for idx, (p, _a, _i) in enumerate(points):
        k = (int(p[0] // cell), int(p[1] // cell), int(p[2] // cell))
        grid.setdefault(k, []).append(idx)
    return grid


def _neighbours(points, grid, p, cell):
    """Everything in the 27 cells around p."""
    base = (int(p[0] // cell), int(p[1] // cell), int(p[2] // cell))
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for idx in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                    out.append(points[idx])
    return out


def point_key(p):
    """A world point as a dictionary key, proof against float drift."""
    return (round(p[0], 1), round(p[1], 1), round(p[2], 1))


def _cradle_partners(point, candidates, self_index, tolerance):
    """The parts whose points surround `point` at (+-10, +-10) on one plane.

    Returns ``(instances, cell)`` - the parts involved and the four surrounding
    points themselves - or empty unless all four quadrants are occupied: a round
    element resting against one or two studs is held by nothing, and only the
    full 2x2 cell grips it. The four points come back because whether the cell
    is already spoken for is the next question asked about it.
    """
    quadrants = {}
    for (p, _a, j) in candidates:
        if j == self_index or abs(p[1] - point[1]) > tolerance:
            continue
        dx, dz = p[0] - point[0], p[2] - point[2]
        if (abs(abs(dx) - CRADLE_OFFSET) > tolerance
                or abs(abs(dz) - CRADLE_OFFSET) > tolerance):
            continue
        quadrants.setdefault((dx > 0, dz > 0), []).append((j, p))

    if len(quadrants) < 4:
        return set(), []
    corners = [entry for group in quadrants.values() for entry in group]
    return {j for j, _p in corners}, [p for _j, p in corners]


def build_graph(flat, library_root, model, tolerance):
    stud_cache, bbox_cache, round_cache = {}, {}, {}
    males, females = [], []      # (point, axis, instance index)
    own = []                     # per instance: its own world points, reused below
    unresolved = set()

    for i, inst in enumerate(flat):
        m, f = part_connection_points(inst.src.part_name, library_root,
                                      stud_cache, bbox_cache, model)
        # "Unresolved" means the part's geometry could not be read - a file
        # missing from the library, a name that resolves to nothing. It does
        # NOT mean the part has no stud connections: a bracket, a curved slope
        # and a tile with a clip on it are all perfectly readable and none of
        # them offers a stud or a plain rectangle of them to sit on. Testing
        # "no mating points" instead reported every one of those as a broken
        # reference, which is a hard fault, on a part the library had answered
        # for in full. The bbox is already cached by the call above, so asking
        # the question properly costs nothing.
        if compute_part_bbox(inst.src.part_name, library_root, bbox_cache,
                             model=model) is None:
            unresolved.add(inst.src.part_name)
        wm, wf = to_world(inst, m), to_world(inst, f)
        own.append((wm, wf))
        for (p, a) in wm:
            males.append((p, a, i))
        for (p, a) in wf:
            females.append((p, a, i))

    # spatial hash over female points, cell = one stud pitch
    cell = STUD_PITCH
    grid = {}
    for idx, (p, a, i) in enumerate(females):
        k = (int(p[0] // cell), int(p[1] // cell), int(p[2] // cell))
        grid.setdefault(k, []).append(idx)

    def dist(a, b):
        return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)

    edges = set()
    connected = set()
    near_miss = {}   # instance index -> smallest non-mating gap seen
    # Connection is not the same question as support, and conflating them hid
    # a whole class of fault. A part counts as CONNECTED the moment anything
    # mates with it - including the part standing *on* it - so a roof whose
    # every slope sat half a stud off the plate below read as fully connected,
    # because the slopes mated with each other. These two answer the other
    # question: did this part's own underside find studs to sit on, and if it
    # did not, how close did it come?
    seated = set()   # its female mated with a male: it is resting on something
    seat_miss = {}   # its female came within a stud pitch of one and missed
    # Which stud is already inside something, and which seating position
    # already has a stud in it. Both are keyed by the world point, because the
    # question they answer later is about a place rather than about a part:
    # whether the four studs of a 2x2 cell are still free.
    taken_studs = {}
    taken_seats = {}

    for (pm, am, i) in males:
        base = (int(pm[0] // cell), int(pm[1] // cell), int(pm[2] // cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for idx in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                        pf, af, j = females[idx]
                        if i == j:
                            continue
                        opposed = (am[0] * af[0] + am[1] * af[1] + am[2] * af[2]) < -0.9
                        if not opposed:
                            continue
                        d = dist(pm, pf)
                        if d <= tolerance:
                            edges.add((min(i, j), max(i, j)))
                            connected.add(i)
                            connected.add(j)
                            seated.add(j)   # j owns the female that took it
                            taken_studs.setdefault(point_key(pm), set()).add(j)
                            taken_seats.setdefault(point_key(pf), set()).add(i)
                        elif d < STUD_PITCH:
                            for k in (i, j):
                                if k not in near_miss or d < near_miss[k]:
                                    near_miss[k] = d
                            # A *seating* miss is a sideways one: the stud is
                            # at the depth this part's underside would take it
                            # at, and in the wrong place across. Measured along
                            # the stud's own axis rather than in plain
                            # distance, because a stud 20 LDU below - one
                            # belonging to a part this one merely stands
                            # beside - is not a seating this part missed, and
                            # counting it flagged a third of every official
                            # set.
                            delta = (pf[0] - pm[0], pf[1] - pm[1], pf[2] - pm[2])
                            along = (delta[0] * am[0] + delta[1] * am[1]
                                     + delta[2] * am[2])
                            across = math.sqrt(max(0.0, d * d - along * along))
                            if abs(along) <= tolerance and across < STUD_PITCH:
                                if j not in seat_miss or across < seat_miss[j]:
                                    seat_miss[j] = across

    # --- round elements cradled in a 2x2 cell -------------------------------
    #
    # Run only for parts the stud grid rejected, which is where this connection
    # always lands: it is legal *because* the part sits half a stud off the
    # grid, so every one of these would otherwise be reported MISALIGNED at
    # 14.14 LDU (10 * sqrt 2, the diagonal of the cell).
    #
    # A 1x1 round element attaches two ways round, and both are the same test
    # against a different set of points: its barrel gripped by the four studs
    # under it, or its own stud entering the tube at the centre of the four
    # receiving positions above it.
    # Every round element is examined, not just the ones that came out
    # unconnected: a brick in the middle of a column mates with its own
    # neighbours perfectly well, and it is the *plate* it is cradled by that
    # ends up with nothing - which is precisely the part that used to be
    # reported for a misalignment it had no part in.
    # A 2x2 cell of studs holds one thing. It can carry a part seated on all
    # four of them, or a round element standing in the gap between them, and
    # never both: the round element fills the very space the part above would
    # come down into. Five studs' worth of plastic on four studs is a model
    # that comes apart in your hands, and neither the grid check nor the
    # collision check sees it - the round element mates legitimately, and its
    # box is round rather than solid, so both pass it. Hence this pass.
    crowded = []

    male_grid = _hash_points(males, cell)
    for i, inst in enumerate(flat):
        if not part_is_round(inst.src.part_name, library_root, round_cache,
                             bbox_cache, model):
            continue

        wm, wf = own[i]
        partners = set()
        occupied = set()
        for (p, _a) in wf:                       # seated on four studs
            held, cell_points = _cradle_partners(
                p, _neighbours(males, male_grid, p, cell), i, tolerance)
            partners |= held
            # those four studs are also holding something else up
            for q in cell_points:
                occupied |= taken_studs.get(point_key(q), set()) - {i}
        for (p, a) in wm:                        # its stud into the tube above
            if a[1] > -0.9:                      # only the stud, not a tube
                continue
            held, cell_points = _cradle_partners(
                p, _neighbours(females, grid, p, cell), i, tolerance)
            partners |= held
            # those four seats already have studs of their own in them, so this
            # one would be the fifth
            for q in cell_points:
                occupied |= taken_seats.get(point_key(q), set()) - {i}

        for j in partners:
            edges.add((min(i, j), max(i, j)))
            connected.add(i)
            connected.add(j)
            # A cradle is a real seating, and it is the one that is legal
            # *because* it is half a stud off - so both ends count as seated
            # or the check below would report exactly what this pass exists
            # to excuse.
            seated.add(i)
            seated.add(j)

        if partners and occupied:
            crowded.append((i, sorted(occupied)))

    # --- one stud, one part ------------------------------------------------
    #
    # A stud goes into one anti-stud. If two different parts both seated on the
    # same stud they are in the same place, and no amount of shape modelling is
    # needed to know it - this is a counting argument, not a volume test.
    #
    # That is worth having because the volume test cannot be trusted here. A
    # bounding box is a bad model of a slope, a wedge, an arch or a bracket, so
    # `collisions._NOT_A_BOX` exempts those families from overlap checking
    # altogether - and the exemption is a blind spot. Two 2x2 slopes placed one
    # stud apart share a full stud of plastic and were reported as a clean
    # model, because "slope" is on that list.
    #
    # Nothing is exempt from this one. It does not care what shape a part is,
    # only that it claimed a stud somebody else had already claimed.
    shared = [(point, sorted(parts)) for point, parts in taken_studs.items()
              if len(parts) > 1]

    return (edges, connected, near_miss, unresolved, crowded, seated,
            seat_miss, shared)


def components(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def fragmentation(flat, comps):
    """
    How many separate connected pieces each submodel's parts fall into.

    A part counts as CONNECTED if it mates with anything at all, so a piece
    that hangs off the part above it while its own seating is off-grid still
    reads as connected. Fragmentation catches that: a submodel that should be
    one solid assembly but lands in six components is broken, whatever the
    per-part status says.
    """
    comp_of = {}
    for n, comp in enumerate(comps):
        for i in comp:
            comp_of[i] = n

    by_sub = {}
    for i, inst in enumerate(flat):
        name = inst.path[-1] if inst.path else inst.src.submodel
        by_sub.setdefault(name, set()).add(comp_of[i])

    sizes = {}
    for i, inst in enumerate(flat):
        name = inst.path[-1] if inst.path else inst.src.submodel
        sizes[name] = sizes.get(name, 0) + 1

    return sorted(((name, len(c), sizes[name]) for name, c in by_sub.items()),
                  key=lambda r: -r[1])


def classify(flat, connected, near_miss, seated=None, seat_miss=None):
    """Per-part status: CONNECTED, MISALIGNED or UNVERIFIED.

    A part is MISALIGNED when its own seating came within a stud pitch of a
    stud and missed - whether or not something else has since been stacked on
    top of it. That last clause is the point: a part holding another part up
    while resting on nothing is the fault this check exists to find, and
    treating "something mates with me" as proof of placement lets every one of
    them through.

    That rule was relaxed once, to excuse any part that something mates with,
    on the evidence that it was wrong more often than right across the
    reference corpus. **It has been put back**, and the reason is worth writing
    down because the relaxation looked safe and was not.

    The argument for it was that the fault survives anyway: a misplaced part
    breaks the seating of whatever it failed to sit on, so the model still
    fails and only the reported line changes. Two worked examples agreed. Both
    were passing for the wrong reason - `near_miss` records against *both*
    halves of a near pair, so what was actually being failed was the innocent
    brick underneath. The moment `near_miss` stopped failing models (below,
    which is where it belongs) the load-bearing case stopped being caught at
    all, and `_buildable_check` said so. Those two examples are pinned there
    for exactly this reason, and they earned their place the first time they
    ran.

    What separates the two tests that were both landing here is the thing that
    was missing all along:

    * **seat_miss** - the part's own underside lined up in depth with a stud
      and missed sideways. A placement question, and it fails a model.
    * **near_miss** - any opposed stud within a stud pitch, in any direction.
      Proximity, not placement. UNVERIFIED.
    """
    seated = seated or set()
    seat_miss = seat_miss or {}
    rows = []
    for i, inst in enumerate(flat):
        aligned = is_axis_aligned(inst.matrix)
        # its underside all but landed on studs, and did not
        unseated = i in seat_miss and i not in seated
        if unseated and aligned:
            status, gap = "MISALIGNED", seat_miss[i]
        elif i in connected:
            status, gap = "CONNECTED", None
        else:
            # `near_miss` - any opposed stud within a stud pitch, in any
            # direction - used to land here as MISALIGNED, and it is not sound
            # enough to fail a model on.
            #
            # It asks nothing about placement. It does not require the stud to
            # be at the depth this part's underside sits at, or to be anywhere
            # near underneath it; it fires on a stud that happens to be within
            # 20 LDU in *any* direction, of a part already known to be
            # stud-disconnected. Whether a model failed therefore came down to
            # how crowded the neighbourhood was. Measured over the reference
            # corpus it was 72% of every remaining complaint, and the parts it
            # named were things like a 1x1 round plate and a tile - sitting in
            # sets that were designed, moulded and sold.
            #
            # UNVERIFIED is what this always meant: no stud connection could be
            # established, and the part may well be held by a joint this tool
            # does not model. It is reported and does not fail the model, which
            # is the honest weight for a test that cannot tell the difference.
            # `seat_miss` above is the sound test and keeps failing models: it
            # requires the part's own underside to have lined up in depth with
            # a stud and missed sideways.
            status, gap = "UNVERIFIED", None
        rows.append((status, inst, gap))
    return rows


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def fmt(inst):
    where = " < ".join(reversed(inst.path)) if inst.path else inst.src.submodel
    loc = f" [in {where}]" if where and where != "__main__" else ""
    return (f"line {inst.src.line_no}: {inst.src.part_name} @ "
            f"({inst.pos[0]:.1f}, {inst.pos[1]:.1f}, {inst.pos[2]:.1f}){loc}")


def print_report(path, rows, comps, unresolved, library_root, tolerance, show_all, rows_flat):
    mis = [r for r in rows if r[0] == "MISALIGNED"]
    unv = [r for r in rows if r[0] == "UNVERIFIED"]
    con = [r for r in rows if r[0] == "CONNECTED"]

    print(f"LDraw stud-connectivity report for: {path}")
    print(f"  library: {library_root or '(none - results will be meaningless)'}")
    print(f"  tolerance: {tolerance} LDU")
    print(f"  parts: {len(rows)}")
    print("-" * 70)
    print(f"  CONNECTED  : {len(con)}")
    print(f"  MISALIGNED : {len(mis)}")
    print(f"  UNVERIFIED : {len(unv)}")
    print("-" * 70)

    if unresolved:
        print(f"NOTE: {len(unresolved)} part(s) had no resolvable geometry:")
        for n in sorted(unresolved)[:10]:
            print(f"    - {n}")
        print("-" * 70)

    if mis:
        print(f"MISALIGNED - off the stud grid by a non-grid amount ({len(mis)}):")
        for _, inst, gap in sorted(mis, key=lambda r: r[2]):
            print(f"  * {fmt(inst)}")
            print(f"    nearest mating point is {gap:.2f} LDU away "
                  f"(needs <= {tolerance})")
        print("-" * 70)
    else:
        print("MISALIGNED: none - every part is either on the grid or held by a "
              "joint this tool does not model.")
        print("-" * 70)

    if show_all and unv:
        print(f"UNVERIFIED - no stud connection found ({len(unv)}):")
        for _, inst, _ in unv:
            print(f"  * {fmt(inst)}")
        print("-" * 70)

    print(f"CONNECTED SUBASSEMBLIES: {len(comps)}  "
          f"(largest {len(comps[0]) if comps else 0} parts)")
    print("-" * 70)

    frag = [r for r in fragmentation(rows_flat, comps) if r[1] > 1]
    if frag:
        print("FRAGMENTED SUBMODELS - parts split across disconnected pieces:")
        for name, npieces, nparts in frag:
            mark = "  <== BROKEN" if npieces >= 3 else ""
            print(f"  {npieces:3d} pieces / {nparts:3d} parts   {name}{mark}")
        print("-" * 70)
    print("Only stud/anti-stud System connections are modelled. Clips, bars, "
          "Technic pins, hinges and bracket fittings all read as UNVERIFIED "
          "even when correct - judge that count against a known-good model. "
          "MISALIGNED is the actionable signal.")


def write_json(path, rows, comps, unresolved, library_root, tolerance, rows_flat):
    data = {
        "library_root": library_root,
        "tolerance": tolerance,
        "counts": {
            "parts": len(rows),
            "connected": sum(1 for r in rows if r[0] == "CONNECTED"),
            "misaligned": sum(1 for r in rows if r[0] == "MISALIGNED"),
            "unverified": sum(1 for r in rows if r[0] == "UNVERIFIED"),
        },
        "misaligned": [
            {"line": inst.src.line_no, "part": inst.src.part_name,
             "position": list(inst.pos), "submodel_path": list(inst.path),
             "nearest_mating_gap_ldu": gap}
            for status, inst, gap in rows if status == "MISALIGNED"
        ],
        "unverified": [
            {"line": inst.src.line_no, "part": inst.src.part_name,
             "position": list(inst.pos), "submodel_path": list(inst.path)}
            for status, inst, gap in rows if status == "UNVERIFIED"
        ],
        "subassembly_sizes": [len(c) for c in comps],
        "fragmented_submodels": [
            {"submodel": n, "pieces": p, "parts": q}
            for n, p, q in fragmentation(rows_flat, comps) if p > 1
        ],
        "unresolved_parts": sorted(unresolved),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Check stud-grid connectivity of an LDraw model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("model")
    ap.add_argument("--library", "-l", default=None,
                    help="LDraw parts library root (folder with 'parts/' and 'p/'). "
                         "Required for meaningful results.")
    ap.add_argument("--tolerance", type=float, default=2.0,
                    help="Max distance (LDU) between a stud and an anti-stud for "
                         "them to count as connected. Default: 2.0")
    ap.add_argument("--per-block", action="store_true",
                    help="Do not expand submodels (legacy coordinate handling).")
    ap.add_argument("--show-unverified", action="store_true",
                    help="List every UNVERIFIED part, not just the counts.")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if not os.path.isfile(args.model):
        print(f"ERROR: file not found: {args.model}", file=sys.stderr)
        sys.exit(2)
    if args.library and not os.path.isdir(args.library):
        print(f"ERROR: --library not a directory: {args.library}", file=sys.stderr)
        sys.exit(2)

    try:
        model = parse_ldr_file(args.model)
    except OSError as e:
        print(f"ERROR: could not read {args.model}: {e}", file=sys.stderr)
        sys.exit(2)

    if not model.instances:
        print("No part instances found - nothing to check.")
        sys.exit(0)

    flat, _ = unflattened(model) if args.per_block else flatten_model(model)

    edges, connected, near_miss, unresolved, crowded, seated, seat_miss = build_graph(
        flat, args.library, model, args.tolerance)
    rows = classify(flat, connected, near_miss, seated, seat_miss)
    comps = components(len(flat), edges)

    print_report(args.model, rows, comps, unresolved, args.library,
                 args.tolerance, args.show_unverified, flat)

    if crowded:
        print(f"\nOVERCROWDED STUDS ({len(crowded)}):")
        print("  A 2x2 cell of studs carries either a part seated on it or a "
              "round element between the studs - never both.")
        for i, others in crowded:
            lines = ", ".join(str(flat[j].src.line_no) for j in others[:4])
            print(f"  line {flat[i].src.line_no:>5}  {flat[i].src.part_name:<16} "
                  f"is in a cell already covered by line{'' if len(others) == 1 else 's'} {lines}")

    if args.json:
        write_json(args.json, rows, comps, unresolved, args.library, args.tolerance, flat)
        print(f"\nJSON report written to: {args.json}")

    sys.exit(1 if any(r[0] == "MISALIGNED" for r in rows) else 0)


if __name__ == "__main__":
    main()
