"""Which stud lattice a part is standing on.

A LEGO model is built on one grid of studs 20 LDU apart. Where that grid
*starts* is arbitrary - but every part in a model has to agree on it, and this
is the module that says whether they do.

# The rule nobody states, and everybody gets wrong

A part's studs are not at its origin. They are at a fixed offset from it, and
the offset depends on whether the part is an even or an odd number of studs
across:

    plate 6 x 6  (3958)   studs at x = ±10, ±30, ±50    - odd multiples of 10
    plate 1 x 4  (3710)   studs at x = ±10, ±30         - odd multiples of 10
    plate 1 x 1  (6141)   stud  at x = 0                - a multiple of 20

So a 6x6 plate placed at x = -180 puts its studs on x ≡ 10 (mod 20), and a 1x1
plate placed at x = 140 puts its stud on x ≡ 0 (mod 20). Both placements are on
multiples of 20. Both look completely reasonable. **They are half a stud apart
and nothing on one can ever connect to anything on the other.**

That is not a rounding error and it is not something being careful prevents. It
is a property of the parts, it is computable from the catalogue, and until now
nothing computed it.

# What a phase is

The phase of a placement on an axis is where its studs fall, modulo 20:

    phase = (position along that axis + any one of its stud offsets) mod 20

Two parts can connect only if they share a phase on x and on z. A model is
sound when every part shares one phase - and the model that prompted this
module had 64 parts on phase 10 and 36 on phase 0, reported as "22 parts off
the stud grid", which is the symptom of one decision described twenty-two
times.

# Half a stud is sometimes deliberate

A jumper plate exists precisely to put a stud half a stud out of phase, and a
part sitting on one is correctly offset. So a mixed phase is a *diagnosis*
rather than a verdict: this module says what the split is and which side is in
the minority, and the callers decide what that is worth. `build_ops` refuses to
add to the minority without being told it is deliberate; `validate_model`
reports it; `autofix` offers to close it.
"""

from collections import Counter

from . import catalog

# Studs are 20 LDU apart, so a phase is a position modulo 20.
PITCH = 20.0
# The only meaningful offset between two lattices: half a stud. Anything else
# is not a phase disagreement, it is a part in the wrong place.
HALF = 10.0


def _footprint(part_id):
    """The part's stud cells as ``[(x, z)]`` in its own coordinates, or None."""
    row = catalog.get_part(str(part_id or "").removesuffix(".dat"))
    if not row:
        return None
    grid = row.get("stud_grid")
    return grid if grid else None


def _rotate(offset, matrix):
    """A footprint offset turned into world space by a 3x3 row-major matrix."""
    x, z = offset
    if not matrix or len(matrix) != 9:
        return x, z
    return (matrix[0] * x + matrix[2] * z,
            matrix[6] * x + matrix[8] * z)


def phase(part_id, x, z, matrix=None):
    """``(phase_x, phase_z)`` for a placement, or None if it has no footprint.

    None is the right answer for anything the stud lattice does not govern - a
    minifigure's arm, a bar in a clip, a part the catalogue has no measurements
    for. Those are held together by something other than studs and judging them
    against a grid is how a correct model gets reported as broken.
    """
    grid = _footprint(part_id)
    if not grid:
        return None
    try:
        x, z = float(x), float(z)
    except (TypeError, ValueError):
        return None

    # Every cell of one part shares a phase - the cells are 20 apart - so one
    # of them answers for all of them.
    offset_x, offset_z = _rotate(grid[0], matrix)
    return (round((x + offset_x) % PITCH, 3),
            round((z + offset_z) % PITCH, 3))


def survey(placements):
    """How many parts stand on each phase.

    ``placements`` is an iterable of ``(part_id, x, z, matrix)``. Returns a dict
    with a Counter per axis and, when an axis is split, which phase is in the
    majority and what would close the gap.
    """
    counts = {"x": Counter(), "z": Counter()}
    for part_id, x, z, matrix in placements:
        found = phase(part_id, x, z, matrix)
        if found is None:
            continue
        counts["x"][found[0]] += 1
        counts["z"][found[1]] += 1

    out = {"counts": {axis: dict(c) for axis, c in counts.items()},
           "judged": sum(counts["x"].values())}
    for axis, c in counts.items():
        if len(c) > 1:
            majority, _ = c.most_common(1)[0]
            out.setdefault("split", {})[axis] = {
                "phases": dict(c),
                "majority": majority,
                "minority_parts": sum(n for p, n in c.items() if p != majority),
            }
    return out


def dominant(placements):
    """The phase most of these parts stand on, as ``(x, z)``, or None."""
    found = survey(placements)
    if not found["judged"]:
        return None
    counts = found["counts"]
    return (Counter(counts["x"]).most_common(1)[0][0],
            Counter(counts["z"]).most_common(1)[0][0])


def correction(current, target):
    """How far to move along one axis to get from ``current`` phase to ``target``.

    The shortest way round the 20 LDU cycle, so a phase 10 out of step comes
    back as ±10 rather than the same distance the long way.
    """
    delta = (target - current) % PITCH
    return delta - PITCH if delta > PITCH / 2 else delta


def describe(part_id, x, z, matrix, target):
    """One line saying how a placement disagrees with ``target``, or None."""
    found = phase(part_id, x, z, matrix)
    if found is None or (found[0] == target[0] and found[1] == target[1]):
        return None
    moves = []
    for index, axis in ((0, "x"), (1, "z")):
        delta = correction(found[index], target[index])
        if delta:
            moves.append(f"{axis} {delta:+g}")
    return ", ".join(moves) or None
