#!/usr/bin/env python3
"""
ldr_validator.py — classify every part-pair relationship in an LDraw model.

WHAT MAKES THIS DIFFERENT FROM THE TWO CHECKERS BESIDE IT
---------------------------------------------------------
`ldr_collision_checker.py` asks a boolean: do these boxes overlap.
`ldr_connectivity_checker.py` asks a boolean: do these studs line up.

Both throw away the quantity that actually decides the question. The single
number this module is built around is the **signed surface separation** `d`
between two parts:

    d < -EPS_OVERLAP        overlapping   — interpenetration, report it
    -EPS_OVERLAP <= d <= +EPS_TOUCH   contact      — AMBIGUOUS
    d > +EPS_TOUCH          separated     — feeds the connection graph

A `collide()` that answers yes/no has already discarded the sign and the
magnitude, which are exactly what tells the three states apart.

WHY THE CONTACT BAND IS AMBIGUOUS
---------------------------------
In LDraw geometry a stud is a radius-6 cylinder and the anti-stud tube that
receives it has inner radius 6. The surfaces are *exactly coincident*: d = 0.
In the real part that is an interference fit — it is what clutch power is. So a
correct connection and an impossible placement land on the same number, and no
tolerance separates them. Geometry alone cannot answer it; connection semantics
must. That is stage 6.

WHY FLOATING IS NOT A PAIRWISE PROPERTY
---------------------------------------
A 40-part subassembly sitting 60 LDU from the model is internally perfect and
has zero collisions, and is still an impossible model. No pairwise test finds
it. Only connected components over a graph of *validated connections* — not
contacts — does. That is stage 7.

THE PIPELINE
------------
    0  parse + flatten
    1  transform sanity      per part, O(n)
    2  duplicate detection   per part, O(n)
    3  lattice alignment     per part, O(n)
    4  broad phase           spatial index
    5  narrow phase          signed distance, per candidate pair
    6  connection resolution per contact-band pair
    7  graph components      whole model

Each stage is cheap relative to the next, so they run in that order.

EXPLICIT NON-GOAL
-----------------
Assembly-order feasibility. A model can pass every stage here — nothing
overlapping, everything connected — and still be unbuildable, because parts
interlock in a closed ring or one is sealed inside another. That is
disassembly path planning and this does not attempt it. It is reported as a
known limitation in the output rather than left implied.

STATUS: NOT WIRED INTO `validate_model`, AND WHY
------------------------------------------------
Every acceptance fixture in `test_ldr_validator.py` passes, including both
false-positive regression tests. Measured against real models it is still not
good enough to replace `maister/agent/validation.py`, and the numbers are here
so that nobody has to rediscover them. Official sets, correct by construction:

    10036 Pizza To Go      town      166 parts    57 components    30 overlaps
    10156 LEGO Truck       town      111 parts    50 components    77 overlaps
    42000 Grand Prix       technic  1377 parts  1347 components   602 overlaps

One component and zero overlaps is the right answer for all three. Two causes,
both in stage 6 rather than in the geometry:

* **The connection model is stud-only.** `part_connection_points` answers for
  studs and anti-studs, which is what it was written for. A clip on a bar, a
  pin in a hole, an axle through a beam, a hinge, a ball in a socket — none of
  them produce a connection point, so every such pair falls to TOUCHING_ONLY
  and the component count becomes roughly the part count. That is what the
  Technic column above is showing.
* **Designed interference reads as overlap.** The same connections are drawn as
  interference fits, and stripping stud and tube primitives does not reach
  them, so the plastic genuinely interpenetrates and stage 5 is right to say so.

The spec anticipates both: it names {stud, axle, pin, clip, bar, ball, socket,
hinge} as the connection types and puts LDCad snap metadata FIRST, with
provenance derivation only as the fallback. The fallback is what is implemented
here. Until the snap data is parsed, this is a good tool for stud-built models
and a noisy one for everything else.

What is in service meanwhile is `maister/agent/occupancy.py`, which sidesteps
the whole problem by measuring shared plastic *volume* against an allowance —
less principled, and tolerant of designed interference in a way that a signed
distance is not. Replacing it means beating its calibration, not just passing
these fixtures.
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ldr_collision_checker as coll          # noqa: E402
import ldr_connectivity_checker as conn       # noqa: E402

try:
    import fcl
except ImportError:                            # pragma: no cover
    fcl = None


# --------------------------------------------------------------------------
# Tolerances
#
# These are three different numbers and conflating them is the mistake the
# whole design is against. Each is separately configurable.
# --------------------------------------------------------------------------

# Upper edge of the contact band: closer than this and two parts are touching
# rather than merely near each other.
EPS_TOUCH = 0.5

# Lower edge: deeper than this and they are inside each other.
#
# It carries a size-dependent floor, because LDraw approximates a circle with
# 16 segments and the sagitta error of that approximation is r*(1 - cos(pi/N)):
#
#     radius            N=16       N=48
#     6 LDU  (stud)     0.115      0.013
#     20 LDU (Technic)  0.384      0.043
#
# Two nominally coincident cylinders turned relative to each other interpenetrate
# by up to that much from faceting alone, with nothing wrong. This is precisely
# why one global tolerance cannot simultaneously clear a small round connection
# and a large one: tighten it and the report floods with faceting, loosen it and
# real overlaps hide. `overlap_floor` scales it with the feature size involved.
EPS_OVERLAP = 0.5

# How close two connection points must be to count as mated. Tight, because
# LDraw connections are exact by construction rather than fitted.
EPS_CONN = 0.5
# ...and how nearly parallel their axes must be, in degrees.
EPS_ANGLE = 1.0

# Sagitta of a 16-segment circle of this radius, used for the overlap floor.
_SEGMENTS = 16


def overlap_floor(radius, segments=_SEGMENTS):
    """How deep two turned cylinders of this radius interpenetrate from faceting."""
    return abs(radius) * (1.0 - math.cos(math.pi / max(3, segments)))


# The stud lattice, and the other increments a real placement lands on.
LATTICE_STEPS = (20.0, 10.0, 8.0, 24.0, 4.0, 2.0)
LATTICE_TOLERANCE = 0.1

# Primitives that ARE the connection. Their surfaces are designed to be
# coincident with what they mate into, so leaving them in the collision mesh
# makes every correct connection register as a hit. Stripped for stage 5 and
# kept for stage 6, which is the stage that needs them.
_CONNECTION_PRIMITIVES = (
    "stud", "stud2", "stud3", "stud4", "stud6", "stud10", "stud12", "stud13",
    "stud15", "stud17", "stud18", "stud21", "stud22",
    "tube", "box4t", "connect", "connhole", "npeghol", "peghole", "axlehole",
    "axleholl", "axlehol8", "axle", "beamhole", "confric", "connect2",
    "connect3", "connect4", "connect5", "connect6",
)


def _is_connection_primitive(name):
    stem = coll.norm_name(name).rsplit("/", 1)[-1].removesuffix(".dat").lower()
    if stem in _CONNECTION_PRIMITIVES:
        return True
    return stem.startswith(("stud", "tube", "connhole", "npeghol", "axlehole",
                            "confric"))


# --------------------------------------------------------------------------
# Stage 0 — parse, flatten, and read real triangles
# --------------------------------------------------------------------------

class Mesh:
    """A part's triangles in its own local frame, tagged by where they came from.

    ``tags`` is parallel to ``tris``: for each triangle, the name of the
    primitive that emitted it. Stage 5 uses it to drop connection geometry and
    stage 6 uses it to synthesise connection points, so it must survive
    flattening rather than being discarded with the reference tree.
    """

    __slots__ = ("tris", "tags", "connectors")

    def __init__(self, tris, tags, connectors):
        self.tris = tris              # (n, 3, 3) float64
        self.tags = tags              # list[str], len n
        self.connectors = connectors  # [(name, local_origin, local_axis)]

    def without_connections(self):
        """The triangles that are structure rather than connection geometry."""
        if self.tris.size == 0:
            return self.tris
        keep = np.fromiter((not _is_connection_primitive(t) for t in self.tags),
                           dtype=bool, count=len(self.tags))
        return self.tris[keep] if keep.any() else self.tris


def _mat(values):
    return np.array(values, dtype=np.float64).reshape(3, 3)


def read_mesh(part_name, library_root, cache, model=None, stack=None):
    """A part's ``Mesh``, recursively expanded. None when it cannot be resolved.

    Cached per part filename: one 3001.dat mesh serves all two hundred
    instances of it, and the instance transform is applied at the collision
    object rather than by re-baking vertices.
    """
    key = coll.norm_name(part_name)
    if key in cache:
        return cache[key]
    if stack is None:
        stack = set()
    if key in stack:
        return None
    stack.add(key)

    lines = coll.get_part_lines(part_name, library_root, model)
    if lines is None:
        cache[key] = None
        stack.discard(key)
        return None

    tris, tags, connectors = [], [], []
    for raw in lines:
        tokens = raw.strip().split()
        if not tokens:
            continue
        kind = tokens[0]

        if kind == "1" and len(tokens) >= 15:
            try:
                offset = np.array([float(v) for v in tokens[2:5]])
                matrix = _mat([float(v) for v in tokens[5:14]])
            except ValueError:
                continue
            sub_name = " ".join(tokens[14:]).strip()
            sub = read_mesh(sub_name, library_root, cache, model, stack)
            if sub is None:
                continue

            if sub.tris.size:
                moved = sub.tris @ matrix.T + offset
                tris.append(moved)
                # Provenance is the nearest CONNECTION-primitive ancestor, not
                # the leaf that authored the triangle. A stud is not drawn by
                # stud.dat — it is drawn by 4-4cyli.dat and 4-4disc.dat inside
                # it — so propagating leaf names tags a stud's triangles
                # "4-4cyli.dat" and stage 5 strips nothing at all. That is not
                # a hypothetical: it left all 700 triangles of a 2x4 brick in
                # the collision mesh and every correct stack registered as a
                # hit.
                if _is_connection_primitive(sub_name):
                    tags.extend([coll.norm_name(sub_name)] * len(sub.tris))
                else:
                    tags.extend(sub.tags)

            # A connection primitive is recorded where it sits, in this part's
            # frame. LDraw authors a stud with its base at y=0 growing to
            # y=-4 (up, since -Y is up), so the reference point is already on
            # the part's surface and its axis is the primitive's local +Y.
            if _is_connection_primitive(sub_name):
                connectors.append((
                    coll.norm_name(sub_name),
                    offset,
                    matrix @ np.array([0.0, 1.0, 0.0]),
                ))
            for name, point, axis in sub.connectors:
                connectors.append((name, matrix @ point + offset, matrix @ axis))

        elif kind in ("3", "4") and len(tokens) >= 11:
            try:
                values = [float(v) for v in tokens[2:]]
            except ValueError:
                continue
            need = 9 if kind == "3" else 12
            if len(values) < need:
                continue
            points = np.array(values[:need]).reshape(-1, 3)
            if kind == "3":
                tris.append(points.reshape(1, 3, 3))
                tags.append(key)
            else:
                # Split the quad into two triangles. BFC and winding are
                # ignored throughout — collision does not care about normals.
                tris.append(np.array([points[[0, 1, 2]], points[[0, 2, 3]]]))
                tags.extend([key, key])

    stack.discard(key)
    mesh = Mesh(np.concatenate(tris) if tris else np.zeros((0, 3, 3)),
                tags, connectors)
    cache[key] = mesh
    return mesh


class Part:
    """One placed part, with everything the later stages ask of it."""

    __slots__ = ("index", "line", "name", "colour", "matrix", "offset",
                 "mesh", "path")

    def __init__(self, index, inst, mesh):
        self.index = index
        self.line = inst.src.line_no
        self.name = inst.src.part_name
        self.colour = inst.src.color
        self.matrix = _mat(inst.matrix)
        self.offset = np.array(inst.pos, dtype=np.float64)
        self.mesh = mesh
        self.path = inst.path

    def world(self, points):
        return points @ self.matrix.T + self.offset

    def ref(self):
        return {"line": self.line, "name": self.name}


def load(path, library_root=None):
    """Stage 0: every placed part with its mesh. Returns ``(parts, notes)``."""
    model = coll.parse_ldr_file(path)
    flat, cycles = coll.flatten_model(model)
    cache = {}
    parts, notes = [], []
    for index, inst in enumerate(flat):
        mesh = read_mesh(inst.src.part_name, library_root, cache, model)
        if mesh is None:
            notes.append({"severity": "warning", "code": "UNRESOLVED",
                          "parts": [{"line": inst.src.line_no,
                                     "name": inst.src.part_name}],
                          "detail": {"why": "no geometry could be found for "
                                            "this part"}})
        parts.append(Part(index, inst, mesh))
    for inst, chain in cycles:
        notes.append({"severity": "error", "code": "CIRCULAR_REFERENCE",
                      "parts": [{"line": inst.line_no,
                                 "name": inst.part_name}],
                      "detail": {"path": list(chain)}})
    return parts, notes


# --------------------------------------------------------------------------
# Stage 1 — transform sanity
# --------------------------------------------------------------------------

def transform_findings(parts):
    """MIRRORED and NON_RIGID, and the scale to propagate for the latter.

    NON_RIGID is deliberately NOT a rejection. Legacy LDraw models legitimately
    use scaled parts — a known LDraw.org case has bricks scaled to about 23 LDU
    tall, and a correct checker reports zero collisions on it once the scale is
    accounted for. So it is flagged, the scale is carried into the geometry for
    the later stages, and the reader decides.
    """
    findings = []
    for part in parts:
        matrix = part.matrix
        determinant = float(np.linalg.det(matrix))
        if determinant < 0:
            findings.append({
                "severity": "warning", "code": "MIRRORED",
                "parts": [part.ref()],
                "detail": {"determinant": round(determinant, 4),
                           "why": "a negative determinant is a reflected part, "
                                  "which cannot be moulded unless the part is "
                                  "symmetric"}})
        product = matrix @ matrix.T
        if not np.allclose(product, np.eye(3), atol=1e-4):
            scales = np.linalg.svd(matrix, compute_uv=False)
            findings.append({
                "severity": "info", "code": "NON_RIGID",
                "parts": [part.ref()],
                "detail": {"scale": [round(float(s), 4) for s in scales],
                           "why": "the part is scaled or sheared. Legal in "
                                  "legacy models; the scale is carried into "
                                  "the collision geometry rather than rejected"}})
    return findings


def axis_aligned(matrix, tolerance=1e-6):
    """Whether every entry is -1, 0 or 1 — a useful secondary signal."""
    return bool(np.all(np.abs(np.abs(matrix) - np.round(np.abs(matrix))) < tolerance)
                and np.all(np.isin(np.round(matrix, 6), (-1.0, 0.0, 1.0))))


# --------------------------------------------------------------------------
# Stage 2 — duplicates
# --------------------------------------------------------------------------

def duplicate_findings(parts, tolerance=1e-3):
    """Pure line comparison, before any geometry is loaded.

    A duplicate is not a collision, and letting it through to stage 5 makes it
    the deepest overlap in the model and buries everything else under it.
    Returns ``(findings, excluded_pairs)``.
    """
    seen = {}
    findings, excluded = [], set()
    for part in parts:
        key = (coll.norm_name(part.name),
               tuple(np.round(part.offset / tolerance).astype(np.int64)),
               tuple(np.round(part.matrix.flatten() / tolerance).astype(np.int64)))
        if key in seen:
            first = seen[key]
            findings.append({
                "severity": "error", "code": "DUPLICATE",
                "parts": [first.ref(), part.ref()],
                "detail": {"why": "the same part at the same transform twice — "
                                  f"delete the one on line {part.line}"}})
            excluded.add((min(first.index, part.index),
                          max(first.index, part.index)))
        else:
            seen[key] = part
    return findings, excluded


# --------------------------------------------------------------------------
# Stage 3 — lattice alignment
# --------------------------------------------------------------------------

def lattice_findings(parts):
    """OFF_LATTICE, at low severity and never as an error.

    A heuristic, and it is reported as one: hinges, angled sections and Technic
    geometry all legitimately leave the lattice.
    """
    findings = []
    for part in parts:
        residuals = []
        for axis, value in zip("xyz", part.offset):
            best = min(abs(value - round(value / step) * step)
                       for step in LATTICE_STEPS)
            if best > LATTICE_TOLERANCE:
                residuals.append({"axis": axis, "value": round(float(value), 3),
                                  "residual": round(float(best), 3)})
        if residuals:
            findings.append({
                "severity": "info", "code": "OFF_LATTICE",
                "parts": [part.ref()],
                "detail": {"off": residuals,
                           "why": "not expressible in stud, plate or jumper "
                                  "increments — often hand-edited, but hinges "
                                  "and Technic geometry do it legitimately"}})
    return findings


# --------------------------------------------------------------------------
# Stages 4 and 5 — broad phase, then signed distance
# --------------------------------------------------------------------------

class _Narrow:
    """FCL BVH models over raw triangles, and the two queries they answer.

    Triangle soup rather than solids, deliberately. LDraw parts are routinely
    non-manifold — open shells, single-sided surfaces, degenerate primitives —
    so mesh booleans fail or return nonsense on them. A BVH does not care.
    """

    def __init__(self, parts, strip_connections=True):
        if fcl is None:
            raise RuntimeError(
                "python-fcl is not installed — `pip install python-fcl`")
        self.parts = parts
        self.objects = {}
        self.boxes = {}
        for part in parts:
            if part.mesh is None:
                continue
            tris = (part.mesh.without_connections() if strip_connections
                    else part.mesh.tris)
            if tris.size == 0:
                continue
            vertices = tris.reshape(-1, 3)
            faces = np.arange(len(vertices)).reshape(-1, 3)
            bvh = fcl.BVHModel()
            bvh.beginModel(len(faces), len(vertices))
            bvh.addSubModel(vertices, faces)
            bvh.endModel()
            self.objects[part.index] = fcl.CollisionObject(
                bvh, fcl.Transform(part.matrix, part.offset))
            # The world AABB is kept here rather than asked of FCL: its Python
            # binding exposes no AABB on a CollisionObject, and the transformed
            # vertices are already in hand.
            world = part.world(vertices)
            self.boxes[part.index] = (world.min(axis=0), world.max(axis=0))

    def candidates(self, dilate=EPS_TOUCH):
        """Stage 4. Pairs whose dilated world AABBs meet.

        All-pairs is O(n^2) and does not scale — a community LDCad collision
        script chokes around forty parts. This is a sweep over sorted intervals
        on the widest axis, which is enough structure to keep the pair count
        near linear on models shaped like real builds.
        """
        boxes = {index: (low - dilate, high + dilate)
                 for index, (low, high) in self.boxes.items()}
        if not boxes:
            return []

        order = sorted(boxes, key=lambda i: boxes[i][0][0])
        out, active = [], []
        for index in order:
            low, high = boxes[index]
            active = [j for j in active if boxes[j][1][0] >= low[0]]
            for j in active:
                other_low, other_high = boxes[j]
                if (high[1] >= other_low[1] and other_high[1] >= low[1]
                        and high[2] >= other_low[2] and other_high[2] >= low[2]):
                    out.append((min(index, j), max(index, j)))
            active.append(index)
        return out

    def separation(self, a, b):
        """Stage 5. The signed surface separation between two parts.

        Two queries rather than one, because neither answers on its own:
        `distance()` measures the gap between disjoint objects and goes to zero
        and stays there once they touch; `collide()` measures penetration depth
        and says nothing about parts that do not. So distance first, and fall
        through to collide for anything it reports as touching — negating the
        depth, which is what makes the result signed.
        """
        first, second = self.objects.get(a), self.objects.get(b)
        if first is None or second is None:
            return None, None

        request = fcl.DistanceRequest(enable_nearest_points=True)
        result = fcl.DistanceResult()
        gap = fcl.distance(first, second, request, result)
        if gap > 1e-9:
            return float(gap), None

        request = fcl.CollisionRequest(num_max_contacts=8, enable_contact=True)
        result = fcl.CollisionResult()
        fcl.collide(first, second, request, result)
        if not result.contacts:
            return 0.0, None
        deepest = max(result.contacts, key=lambda c: c.penetration_depth)
        return -float(deepest.penetration_depth), \
            [round(float(v), 2) for v in deepest.pos]

    def separating_move(self, a, b):
        """The shortest translation that pulls two parts apart, in LDU.

        FCL's contact depth is a per-triangle-pair number along that pair's
        contact normal, not a minimum translation distance for the two solids:
        two 2x4 bricks sharing 10 LDU along x report 40, because a triangle on
        the far face of one passes right through the other. Correct, and not
        the number a reader can act on.

        The world AABB overlap gives the actionable one — the shallowest axis
        is the way out — which is the same arithmetic `collisions._describe`
        does. Reported alongside the signed distance rather than instead of it:
        the distance decides the band, this says how far to move.
        """
        first, second = self.boxes.get(a), self.boxes.get(b)
        if first is None or second is None:
            return None, None
        low = np.maximum(first[0], second[0])
        high = np.minimum(first[1], second[1])
        overlap = high - low
        if np.any(overlap <= 0):
            return None, None
        axis = int(np.argmin(overlap))
        return float(overlap[axis]), "xyz"[axis]


def band(distance, eps_touch=EPS_TOUCH, eps_overlap=EPS_OVERLAP):
    if distance is None:
        return None
    if distance < -eps_overlap:
        return "OVERLAP"
    if distance <= eps_touch:
        return "CONTACT"
    return "SEPARATED"


# --------------------------------------------------------------------------
# Stage 6 — connection resolution
# --------------------------------------------------------------------------

_MALE = {"stud", "axle", "pin", "bar", "ball"}
_FEMALE = {"tube", "socket", "clip", "hole", "axlehole"}


def _connector_kind(name):
    """``(type, gender)`` for a connection primitive."""
    stem = name.rsplit("/", 1)[-1].removesuffix(".dat").lower()
    if stem.startswith("stud"):
        # stud2/stud3/stud4 are the open/tube forms — the receiving side.
        return ("stud", "female" if stem[4:5].isdigit() and stem[4] in "234"
                else "male")
    if stem.startswith("tube"):
        return "stud", "female"
    if "axlehole" in stem or stem.startswith("axlehol"):
        return "axle", "female"
    if stem.startswith("axle"):
        return "axle", "male"
    if "peghol" in stem or "connhole" in stem or "confric" in stem:
        return "pin", "female"
    if stem.startswith("connect"):
        return "pin", "male"
    return "stud", "male"


def connection_points(part, library_root, caches, model=None):
    """Every connection point of a placed part, in world space.

    The spec's fallback is to synthesise these from primitive provenance, and
    stage 0 records exactly what that needs. It is still not enough, and the
    reason is worth writing down because it looks like it should be:

    **A tube's position is not a receiving position.** A 2x2 brick has one
    central tube at (0, 0) that grips four studs at (+-10, +-10), so reading
    the female points off the tube geometry puts them where nothing mates. Worse
    the other way: a 6x6 plate has 36 studs and 25 tubes, and the tubes are
    authored as flipped stud primitives — count those as studs and you have 25
    male points that can never mate with anything.

    `ldr_connectivity_checker.part_connection_points` already solves this: males
    are the stud primitives that are not facing down, females are the part's own
    stud grid projected onto its bottom face, unioned with the footprint lattice
    and the jumper cases. That is a more correct connection set than provenance
    alone can give, so it is used rather than reimplemented worse here.
    """
    males, females = conn.part_connection_points(
        part.name, library_root, caches["studs"], caches["bbox"], model)

    out = []
    for points, gender in ((males, "male"), (females, "female")):
        for position, axis in points:
            world_point = part.matrix @ np.array(position, dtype=np.float64) \
                + part.offset
            world_axis = part.matrix @ np.array(axis, dtype=np.float64)
            norm = float(np.linalg.norm(world_axis))
            if norm < 1e-9:
                continue
            out.append((world_point, world_axis / norm, "stud", gender))
    return out


def connected(part_a, part_b, points_a, points_b,
              eps_conn=EPS_CONN, eps_angle=EPS_ANGLE):
    """Whether some connection point of A mates with one of B."""
    if not points_a or not points_b:
        return False
    cos_limit = math.cos(math.radians(180.0 - eps_angle))
    for pos_a, axis_a, kind_a, gender_a in points_a:
        for pos_b, axis_b, kind_b, gender_b in points_b:
            if kind_a != kind_b or gender_a == gender_b:
                continue
            if np.linalg.norm(pos_a - pos_b) > eps_conn:
                continue
            alignment = float(np.dot(axis_a, axis_b))
            # parallel or antiparallel, both count: which way a primitive's
            # local +Y ends up pointing depends on how the part was authored.
            if abs(alignment) >= abs(cos_limit):
                return True
    return False


# --------------------------------------------------------------------------
# Stage 7 — the connection graph
# --------------------------------------------------------------------------

def components(count, edges):
    parent = list(range(count))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups = defaultdict(list)
    for i in range(count):
        groups[find(i)].append(i)
    return sorted(groups.values(), key=len, reverse=True)


def _bbox(parts, indices):
    points = []
    for i in indices:
        part = parts[i]
        if part.mesh is not None and part.mesh.tris.size:
            points.append(part.world(part.mesh.tris.reshape(-1, 3)))
        else:
            points.append(part.offset.reshape(1, 3))
    if not points:
        return None
    stacked = np.concatenate(points)
    return {"min": [round(float(v), 1) for v in stacked.min(axis=0)],
            "max": [round(float(v), 1) for v in stacked.max(axis=0)]}


# --------------------------------------------------------------------------
# The whole thing
# --------------------------------------------------------------------------

def validate(path, library_root=None, eps_touch=EPS_TOUCH,
             eps_overlap=EPS_OVERLAP, eps_conn=EPS_CONN,
             eps_angle=EPS_ANGLE, strip_connections=True):
    """Run every stage. Returns the report described in the module docstring."""
    model = coll.parse_ldr_file(path)
    parts, findings = load(path, library_root)
    if not parts:
        # Same shape as the full summary below: a caller that reads
        # `connections` must not crash on an empty file.
        return {"summary": {"parts": 0, "components": 0, "overlaps": 0,
                            "warnings": 0, "connections": 0,
                            "pairs_tested": 0},
                "findings": findings,
                "limitations": _LIMITATIONS}

    findings += transform_findings(parts)
    duplicates, skip = duplicate_findings(parts)
    findings += duplicates
    findings += lattice_findings(parts)

    pairs, overlaps, touching = [], 0, []
    edges = []
    if fcl is not None:
        narrow = _Narrow(parts, strip_connections=strip_connections)
        cached_points = {}
        caches = {"studs": {}, "bbox": {}}

        def points_for(index):
            if index not in cached_points:
                try:
                    cached_points[index] = connection_points(
                        parts[index], library_root, caches, model)
                except Exception:
                    cached_points[index] = []
            return cached_points[index]

        for a, b in narrow.candidates(dilate=eps_touch):
            if (a, b) in skip:
                continue
            distance, contact = narrow.separation(a, b)
            if distance is None:
                continue
            where = band(distance, eps_touch, eps_overlap)
            move, axis = narrow.separating_move(a, b)

            # Two parts resting face to face are COPLANAR, and FCL measures the
            # in-plane overlap of two coplanar triangles as a penetration: a
            # brick sitting correctly on another but three studs along comes
            # back "3 LDU deep" — the 3 is the sideways offset, not a depth.
            #
            # The world AABBs settle it. A box encloses its geometry, so boxes
            # that do not interpenetrate are a proof that the parts do not
            # either. This can only ever remove a false overlap; it can never
            # hide a real one.
            if where == "OVERLAP" and move is None:
                where = "CONTACT"

            pairs.append((a, b, distance, where))

            if where == "OVERLAP":
                overlaps += 1
                findings.append({
                    "severity": "error", "code": "OVERLAP",
                    "parts": [parts[a].ref(), parts[b].ref()],
                    "detail": {
                        # What to do about it: the shortest way apart.
                        "penetration_ldu": round(move, 2) if move else
                                           round(-distance, 2),
                        "separate_along": axis,
                        # ...and the raw signed distance the band was decided
                        # on, which is a different quantity. See separating_move.
                        "contact_depth_ldu": round(-distance, 2),
                        "contact_point": contact}})
            elif where == "CONTACT":
                if connected(parts[a], parts[b], points_for(a), points_for(b),
                             eps_conn, eps_angle):
                    edges.append((a, b))
                else:
                    touching.append((a, b, distance))

    for a, b, distance in touching:
        findings.append({
            "severity": "warning", "code": "TOUCHING_ONLY",
            "parts": [parts[a].ref(), parts[b].ref()],
            "detail": {"separation_ldu": round(distance, 2),
                       "why": "these two touch but no connection point of one "
                              "mates with the other. Legal — parts may abut — "
                              "but a large abutting contact with nothing "
                              "holding it is a common signature of "
                              "misplacement"}})

    groups = components(len(parts), edges)
    for group in groups[1:]:
        if len(group) == 1:
            part = parts[group[0]]
            findings.append({
                "severity": "error", "code": "FLOATING_PART",
                "parts": [part.ref()],
                "detail": {"why": "nothing connects to this part"}})
        else:
            findings.append({
                "severity": "error", "code": "FLOATING_SUBASSEMBLY",
                "parts": [parts[i].ref() for i in group[:6]],
                "detail": {"parts_in_piece": len(group),
                           "bbox": _bbox(parts, group),
                           "why": "this group is internally connected and "
                                  "joined to nothing else in the model"}})

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return {
        "summary": {
            "parts": len(parts),
            "components": len(groups),
            "overlaps": overlaps,
            "warnings": sum(1 for f in findings if f["severity"] == "warning"),
            "connections": len(edges),
            "pairs_tested": len(pairs),
        },
        "findings": findings,
        "limitations": _LIMITATIONS,
    }


_LIMITATIONS = [
    "Assembly order is not checked. A model can pass every stage here and "
    "still be unbuildable, because parts interlock in a closed ring or one is "
    "sealed inside another. That is disassembly path planning and is out of "
    "scope.",
    "Connection points are derived from primitive provenance rather than read "
    "from LDCad snap metadata, so a part whose connections are not expressed "
    "as standard primitives may read as TOUCHING_ONLY.",
]


def summarise(report):
    """The human half of the output."""
    summary = report["summary"]
    lines = [
        f"{summary['parts']} parts, {summary.get('connections', 0)} validated "
        f"connections, {summary['components']} component(s)",
    ]
    counts = defaultdict(int)
    for finding in report["findings"]:
        counts[finding["code"]] += 1
    if not counts:
        lines.append("no findings")
    for code, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {count:4}  {code}")
    for finding in report["findings"][:20]:
        where = ", ".join(f"line {p['line']} {p['name']}"
                          for p in finding["parts"][:2])
        lines.append(f"  [{finding['severity']:7}] {finding['code']:22} {where}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Classify every part-pair relationship in an LDraw model.")
    parser.add_argument("path")
    parser.add_argument("--library", default=None,
                        help="LDraw library root (the folder holding parts/ and p/)")
    parser.add_argument("--eps-touch", type=float, default=EPS_TOUCH)
    parser.add_argument("--eps-overlap", type=float, default=EPS_OVERLAP)
    parser.add_argument("--eps-conn", type=float, default=EPS_CONN)
    parser.add_argument("--eps-angle", type=float, default=EPS_ANGLE)
    parser.add_argument("--keep-connection-geometry", action="store_true",
                        help="do not strip studs and tubes before colliding")
    parser.add_argument("--json", action="store_true", help="machine-readable only")
    args = parser.parse_args(argv)

    report = validate(args.path, args.library, args.eps_touch, args.eps_overlap,
                      args.eps_conn, args.eps_angle,
                      strip_connections=not args.keep_connection_geometry)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(summarise(report))
    return 1 if any(f["severity"] == "error" for f in report["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
