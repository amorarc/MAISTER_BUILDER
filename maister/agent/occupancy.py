"""Which parts really share plastic, measured from the parts' own geometry.

The collision check used to ask a bounding box, and then ask a list of words
whether the bounding box could be believed. Both halves of that fail on exactly
the parts a good model is made of.

A box is a poor likeness of a slope, a wedge, a bracket or a dish, so those
were exempted - and with them went every defect between two of them. Measured
over the 65 project models on disk: of 797 overlaps deeper than a legitimate
contact, **633 were never judged at all**, 432 of them because both parts were
exempt. Two 2x2 slopes sharing a full stud of plastic, a 2x4 brick buried
inside a double slope, a bracket driven through a brick - `validate_model`
answered "nothing overlaps" to all of them. Only 141 of the catalogue's 5,878
parts could ever be judged.

It cannot be fixed by editing the word list, because the word list is the
smaller half of the problem: 86.8% of the catalogue never reaches it, having
already failed to be exactly one brick or one plate tall.

# What this does instead

It measures the plastic.

    the triangles          already parsed, and thrown away - see `triangles`
    -> a surface           rasterised into a grid at RESOLUTION LDU
    -> a solid             flood-fill the outside; the rest is plastic
    -> a core              erode one voxel, so touching is not sharing
    -> shared volume       count the voxels two placed parts have in common

The load-bearing fact is a property of the real bricks rather than of this
code: **correctly assembled LEGO parts never share material.** A stud goes
*into* a hollow tube; a bar goes *through* a clip; a bracket holds a plate
*beside* its upstand; a dish *nests* in another dish. Every one of those is an
overlap of bounding boxes and none of them is an overlap of plastic. So a
measurement of plastic needs no exemptions at all - not for shape, not for
rotation, not for the parts whose sockets swallow more than a stud.

Measured on the cases that defeated the old check:

    two 2x2 slopes in one place        6248     slopes side by side        0
    two 2x2 slopes one stud apart      1888     plate stacked on brick     0
    2x4 brick in a 2x4 double slope    2640     two bricks stacked         0
    two 4x4 dishes in one place        9792     plate on a bracket       216

Nine times between the worst correct build and the mildest real defect, where
the old check could not see any of the four on the left.

# Why voxels and not triangles

Triangle-triangle intersection is the obvious answer and it is the one the
LDraw community tried and backed away from: the tolerance band is the whole
problem, and rotated or round parts produce false intersections that cannot be
tuned away without losing real ones. A voxel grid has its tolerance built in -
it is the voxel - and the erosion step turns "these two surfaces touch", which
is what every correct connection looks like, into a measurement of zero.

It is also what the generative-LEGO literature settled on for the same reason.

# Cost

One rasterisation per distinct part, ~1-20 ms, cached for the life of the
process; a model uses a handful of distinct parts however many placements it
has. The pair test is a numpy gather over the smaller part's occupied voxels
and runs in microseconds. Both worst-case project models on disk voxelise and
judge every candidate pair in under a fifth of a second.
"""

import numpy as np

# LDU per voxel. A stud is 20, a plate 8, a stud's engagement 4, and a brick
# wall about 4.
#
# 2 was the first choice and it is not enough, which is worth recording because
# the reasoning for it sounded right: it resolves every feature a LEGO part
# has. What it does not resolve is the *error*, and the error is what decides a
# threshold. Two parts correctly side by side share a skin of voxels along
# their contact face, and that skin scales with the area they touch - a big
# Technic brick against its neighbour came to 1,352 cubic LDU of pure
# quantisation, against a mildest-real-defect of 1,888. A 1.4x band is not a
# band, it is a coincidence.
#
# Halving it costs eight times the voxels and buys back far more than eight
# times the confidence, because the error is a surface and the signal is a
# volume: the same false pair falls to 390 while the same real defect rises to
# 3,607. That is a 9.2x band, and it is what makes an absolute threshold safe.
#
#     resolution   worst false positive   mildest real defect   band
#     2 LDU                        1352                  1888   1.4x
#     1 LDU                         390                  3607   9.2x
RESOLUTION = 1.0

# A part bigger than this is not rasterised. 40 million voxels is a 32x32
# baseplate with room to spare; past it the answer is "unchecked" rather than a
# gigabyte of RAM. See `solid`.
MAX_VOXELS = 40_000_000

# Shared plastic, in cubic LDU, above which two parts are in each other rather
# than beside each other.
#
# Set in the middle of the band the resolution above buys: about three times
# the worst false positive measured (390, two Technic bricks correctly side by
# side in 10295 Porsche) and about a third of the mildest real defect (3,607,
# two 2x2 slopes a stud apart). Being wrong by a factor of two in either
# direction changes nothing, which is the property to want in a threshold.
#
# For scale, a whole 1x1 plate is 20 x 20 x 8 = 3,200 cubic LDU.
#
# This is the *ceiling*, not the whole rule. Being absolute, it makes the
# check's sensitivity a function of part size - which is why a 1x1 brick buried
# inside a 2x2 slope (1,009 cubic LDU) went unreported for as long as this was
# the only test. `collisions.SHARED_FRACTION` lowers it per pair, on the share
# of the smaller part's own plastic; that is where the reasoning and the
# measurements for it live.
SHARED_LDU3 = 1200.0

# The neighbourhood the erosion peels away: one voxel in every direction,
# including diagonals. Without it two parts resting on one another share a
# skin of surface voxels along the whole contact face, which grows with the
# area they touch and has nothing to do with how far they interpenetrate.
_NEIGHBOURHOOD = np.ones((3, 3, 3), dtype=bool)


class _Unavailable(Exception):
    """This part's geometry cannot be turned into a solid."""


def _triangles(part_name, library_root, coll, cache, stack=None, model=None):
    """Every triangle of a part, in its own local frame, recursively.

    The same walk `compute_part_points` already makes - it reads these very
    lines and keeps only the corners. A vertex cloud cannot say which side of a
    surface is inside, so the connectivity between them, which the file states
    and which costs nothing to keep, is what this holds on to.
    """
    key = coll.norm_name(part_name)
    if key in cache:
        return cache[key]
    stack = set() if stack is None else stack
    if key in stack:
        return []                      # circular reference; the guard is real
    stack.add(key)

    lines = coll.get_part_lines(part_name, library_root, model)
    if lines is None:
        cache[key] = None
        stack.discard(key)
        return None

    out = []
    for line in lines:
        tokens = line.strip().split()
        if not tokens:
            continue
        kind = tokens[0]

        if kind == "1" and len(tokens) >= 14:
            try:
                shift = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
                matrix = [float(v) for v in tokens[5:14]]
            except ValueError:
                continue
            sub = _triangles(" ".join(tokens[14:]).strip(), library_root, coll,
                             cache, stack, model)
            if not sub:
                continue
            for triangle in sub:
                out.append(tuple(
                    tuple(a + b for a, b in
                          zip(coll.mat_vec_mul(matrix, point), shift))
                    for point in triangle))

        elif kind in ("3", "4"):
            # A quad is two triangles. Type 2 and 5 are edge lines: they carry
            # no surface, and a line rasterised as geometry would wall off the
            # flood fill from a cavity that is genuinely open.
            corners = 3 if kind == "3" else 4
            try:
                values = [float(v) for v in tokens[2:2 + corners * 3]]
            except ValueError:
                continue
            if len(values) < corners * 3:
                continue
            points = [tuple(values[i * 3:i * 3 + 3]) for i in range(corners)]
            out.append((points[0], points[1], points[2]))
            if corners == 4:
                out.append((points[0], points[2], points[3]))

    stack.discard(key)
    cache[key] = out or None
    return cache[key]


def _rasterise(triangles, resolution):
    """``(origin, shell)`` - the voxels any surface of the part passes through.

    Each triangle is sampled on its own barycentric grid at half a voxel, which
    is dense enough that a surface cannot slip between two voxels. Crude beside
    a proper conservative rasteriser, and it holds for LDraw because every
    surface here is a flat facet of a part a few hundred LDU across.
    """
    tri = np.asarray(triangles, dtype=np.float64)
    flat = tri.reshape(-1, 3)
    # One voxel of margin all round, so the flood fill has somewhere to start
    # and can reach every face of the part.
    low = flat.min(axis=0) - resolution
    high = flat.max(axis=0) + resolution
    dims = np.maximum(np.ceil((high - low) / resolution).astype(int) + 1, 3)
    if int(np.prod(dims.astype(np.int64))) > MAX_VOXELS:
        raise _Unavailable("too large to rasterise")

    shell = np.zeros(tuple(dims), dtype=bool)
    edge1 = tri[:, 1] - tri[:, 0]
    edge2 = tri[:, 2] - tri[:, 0]
    longest = np.maximum(np.linalg.norm(edge1, axis=1),
                         np.linalg.norm(edge2, axis=1))
    steps = np.clip(np.ceil(longest / (resolution * 0.5)).astype(int) + 1, 2, 96)

    # Grouped by sample count so the barycentric grid is built once per group
    # rather than once per triangle.
    for count in np.unique(steps):
        chosen = steps == count
        axis = np.linspace(0.0, 1.0, int(count))
        u, v = np.meshgrid(axis, axis, indexing="ij")
        inside = (u + v) <= 1.0
        u, v = u[inside][None, :], v[inside][None, :]
        points = (tri[chosen, 0][:, None, :]
                  + edge1[chosen][:, None, :] * u[..., None]
                  + edge2[chosen][:, None, :] * v[..., None]).reshape(-1, 3)
        index = np.floor((points - low) / resolution).astype(int)
        np.clip(index, 0, dims - 1, out=index)
        shell[index[:, 0], index[:, 1], index[:, 2]] = True

    return low, shell


def _fill(shell):
    """The solid: the shell, plus everything the outside cannot reach.

    This is the step that makes a hollow part hollow. A brick's underside, a
    stud's tube, the arch under a mudguard and the space inside a bracket's L
    are all open to the outside, so the fill never claims them - which is
    exactly why a stud sitting in a tube shares nothing with it.
    """
    from scipy import ndimage

    free = ~shell
    labels, count = ndimage.label(free)
    if count == 0:
        return shell

    # Every region touching a face of the grid is outside. The grid was padded
    # by a voxel, so the outside is guaranteed to be one of them.
    edges = np.concatenate([
        labels[0].ravel(), labels[-1].ravel(),
        labels[:, 0].ravel(), labels[:, -1].ravel(),
        labels[:, :, 0].ravel(), labels[:, :, -1].ravel(),
    ])
    outside = np.unique(edges)
    outside = outside[outside != 0]
    exterior = np.isin(labels, outside)
    return shell | (free & ~exterior)


class Solid:
    """A part's plastic, as a voxel grid in the part's own local frame.

    Two erosions are kept, and which one is used depends on how the pair is
    placed relative to each other - see ``shared_volume``.

    ``core`` is one voxel in from the surface. That is the right margin when
    both parts sit square to the grid, because the two grids are then parallel
    and the only error is the half-voxel of the sampling itself.

    ``loose`` is two, and it exists because a rotated pair is resampled rather
    than aligned: B's voxel centres land anywhere inside A's cells, so the
    boundary is uncertain by about a voxel diagonal - 1.7 LDU at this
    resolution - and a large contact face turns that into a few hundred voxels
    of shared plastic that is not there. Measured on 76161 Batwing, whose wing
    panels are pinned at 19 degrees: 2,904 cubic LDU of pure resampling error,
    reported as sixty-nine overlaps in a set that is correctly modelled.
    """

    __slots__ = ("origin", "core", "loose", "resolution", "voxel_volume")

    def __init__(self, origin, core, loose, resolution):
        self.origin = np.asarray(origin, dtype=np.float64)
        self.core = core
        self.loose = loose
        self.resolution = resolution
        self.voxel_volume = resolution ** 3

    def grid(self, tight=True):
        return self.core if tight else self.loose

    def centres(self, tight=True):
        """World-frame-ready centres of every voxel of plastic, part-local."""
        filled = np.argwhere(self.grid(tight))
        if not len(filled):
            return np.empty((0, 3), dtype=np.float64)
        return self.origin + (filled + 0.5) * self.resolution


def solid(part_name, library_root, coll, cache, model=None,
          resolution=RESOLUTION):
    """The ``Solid`` for a part, or None when its geometry will not resolve.

    ``cache`` is the caller's dict and holds both the triangles and the solids,
    under separate keys, so a model with sixty 1x1 round bricks in it rasterises
    one.
    """
    key = coll.norm_name(part_name)
    solids = cache.setdefault("__solids__", {})
    if key in solids:
        return solids[key]

    try:
        triangles = _triangles(part_name, library_root, coll, cache, model=model)
        if not triangles:
            raise _Unavailable("no surfaces")
        origin, shell = _rasterise(triangles, resolution)
        filled = _fill(shell)
        from scipy import ndimage

        core = ndimage.binary_erosion(filled, _NEIGHBOURHOOD)
        if not core.any():
            # Everything this part has is skin - a sticker, a flag, a part one
            # voxel thick. There is no interior to be inside of, so it is not
            # something this check can speak about.
            raise _Unavailable("nothing but surface")
        loose = ndimage.binary_erosion(core, _NEIGHBOURHOOD)
        found = Solid(origin, core, loose, resolution)
    except _Unavailable:
        found = None
    except Exception:
        # Malformed geometry, a part the library cannot resolve, scipy raising
        # on a degenerate grid. Unchecked is the honest answer and the caller
        # reports it as such; it must never be mistaken for "clean".
        found = None

    solids[key] = found
    return found


def core_volume(part_name, library_root, coll, cache, model=None,
                resolution=RESOLUTION):
    """Cubic LDU of plastic in one part, or None when it cannot be measured.

    The same eroded core the pair test counts against, so "they share a fifth
    of it" is a fifth of the number this returns and not of some other reading
    of the part. Cached with the solid, so asking costs nothing after the
    first time.
    """
    found = solid(part_name, library_root, coll, cache, model, resolution)
    if found is None:
        return None
    return float(found.core.sum()) * found.voxel_volume


def aligned(instance, tolerance=1e-3):
    """Whether a placement is square to the grid, up to LDraw's 3-dp rounding.

    Deliberately slack: an official set writes a quarter turn as `0.966` and
    `0.259` when it means 15 degrees, but it writes a right angle as exactly 1
    and 0. What this has to separate is "parallel to the other grid" from "at
    some angle to it", and the rounding in a right angle is nowhere near the
    tolerance.
    """
    return all(abs(v) <= tolerance or abs(abs(v) - 1.0) <= tolerance
               for v in instance.matrix)


def shared_volume(inst_a, inst_b, library_root, coll, cache, model=None,
                  resolution=RESOLUTION):
    """Cubic LDU of plastic two placed parts have in common, or None.

    None means at least one of them could not be measured - which is a third
    answer, distinct from nothing-shared, and the caller has to keep it that
    way.
    """
    solid_a = solid(inst_a.src.part_name, library_root, coll, cache, model,
                    resolution)
    solid_b = solid(inst_b.src.part_name, library_root, coll, cache, model,
                    resolution)
    if solid_a is None or solid_b is None:
        return None

    # Parallel grids or resampled ones. Both square to the world means both
    # square to each other, and the sampling is then exact to half a voxel; a
    # pair at any other angle needs the wider margin. See ``Solid``.
    tight = aligned(inst_a) and aligned(inst_b)

    # The smaller part's voxels are the ones carried across, so the work is set
    # by the smaller of the pair rather than by the larger.
    if int(solid_b.grid(tight).sum()) > int(solid_a.grid(tight).sum()):
        inst_a, inst_b = inst_b, inst_a
        solid_a, solid_b = solid_b, solid_a

    points = solid_b.centres(tight)
    if not len(points):
        return 0.0

    matrix_a = np.asarray(inst_a.matrix, dtype=np.float64).reshape(3, 3)
    matrix_b = np.asarray(inst_b.matrix, dtype=np.float64).reshape(3, 3)
    position_a = np.asarray(inst_a.pos, dtype=np.float64)
    position_b = np.asarray(inst_b.pos, dtype=np.float64)

    # B-local -> world -> A-local. The inverse of a placement matrix is its
    # transpose: LDraw placements are rotations, possibly with a reflection,
    # and both are orthonormal. A part placed with a scale would break that,
    # and there are none - a scaled brick is not a brick.
    world = points @ matrix_b.T + position_b
    local = (world - position_a) @ matrix_a

    grid_a = solid_a.grid(tight)
    index = np.floor((local - solid_a.origin) / solid_a.resolution).astype(int)
    shape = np.array(grid_a.shape)
    inside = np.all((index >= 0) & (index < shape), axis=1)
    if not inside.any():
        return 0.0
    index = index[inside]
    hits = grid_a[index[:, 0], index[:, 1], index[:, 2]]
    return float(hits.sum()) * solid_b.voxel_volume
