"""How big a model is, and where it sits.

The assembly pass needs one thing the checkers never had to expose: the extent
of a whole model in world space. Three subconstructions become one scene by
being pushed apart until their boxes stop touching, and that arithmetic needs
real numbers - a tree that is 9 studs wide has to move 9 studs, not the 4 the
agent guessed.

Measured from the same flattened geometry the collision checker uses, so a
model's footprint here and its overlaps there can never disagree.
"""

import sys

from .config import CHECKER_DIR
from .library import ensure_library_root

if str(CHECKER_DIR) not in sys.path:
    sys.path.insert(0, str(CHECKER_DIR))

import ldr_collision_checker as coll  # noqa: E402

LDU_PER_STUD = 20.0
# Gap left between two subconstructions standing side by side. One stud of air
# reads as "these are separate things" without pushing a three-object scene
# across half a baseplate.
SPACING_LDU = 20.0


def _points(part_name, library, cache, model):
    """A part's local vertices, falling back to a generic 1x1 brick.

    An unresolvable part is measured as a brick rather than skipped: a model
    whose bounding box silently omits a part would be laid out overlapping the
    thing next to it, which is worse than being 20 LDU out.
    """
    try:
        points = coll.compute_part_points(part_name, library, cache, model=model)
    except Exception:
        points = None
    return points or coll.GENERIC_POINTS


def measure_text(text, name="model"):
    """Bounding box of LDraw source held in memory."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="measure-") as tmp:
        path = Path(tmp) / f"{name}.ldr"
        path.write_text(text, encoding="utf-8")
        return measure(path)


def measure(path):
    """The world-space extent of a model file.

    Returns a dict with the box in LDU, its size in LDU and in studs, and the
    ground level - or ``{"error": ...}`` for a file that holds no parts.
    """
    library_root = ensure_library_root()
    library = str(library_root) if library_root else None

    try:
        model = coll.parse_ldr_file(str(path))
    except OSError as exc:
        return {"error": f"could not read {path}: {exc}"}

    flat, _ = coll.flatten_model(model)
    if not flat:
        return {"error": "no part references (type-1 lines) in this file"}

    cache = {}
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for instance in flat:
        points = _points(instance.src.part_name, library, cache, model)
        (lo, hi) = coll.world_aabb(instance, points, 1.0)
        lows = [min(a, b) for a, b in zip(lows, lo)]
        highs = [max(a, b) for a, b in zip(highs, hi)]

    size = [round(h - l, 2) for l, h in zip(lows, highs)]
    return {
        "parts": len(flat),
        "min": [round(v, 2) for v in lows],
        "max": [round(v, 2) for v in highs],
        "size_ldu": {"x": size[0], "y": size[1], "z": size[2]},
        "size_studs": {
            "width": round(size[0] / LDU_PER_STUD, 2),
            "depth": round(size[2] / LDU_PER_STUD, 2),
            # height reads in bricks more naturally than in studs
            "height_bricks": round(size[1] / 24.0, 2),
        },
        # -Y is up, so the lowest point of the build is the largest y
        "ground_y": round(highs[1], 2),
        "top_y": round(lows[1], 2),
    }


def _snap(value):
    """To the nearest stud, so a computed placement stays on the grid."""
    return round(value / LDU_PER_STUD) * LDU_PER_STUD


def layout(boxes, spacing=SPACING_LDU):
    """Where to put each component so that none of them overlap.

    ``boxes`` is a list of ``(name, measurement)``. They are placed in a row
    along x in the order given, each one's ground level dropped onto y = 0, and
    every offset snapped to the stud grid - a scene assembled off-grid would
    fail validation for reasons that have nothing to do with how it was built.

    Returns ``{name: (dx, dy, dz)}``, the translation to apply to that
    component's own coordinates.
    """
    placements = {}
    cursor = 0.0

    for name, box in boxes:
        if not box or box.get("error"):
            placements[name] = (0.0, 0.0, 0.0)
            continue

        low, high = box["min"], box["max"]
        # left edge to the cursor, centred on z, standing on y = 0
        dx = _snap(cursor - low[0])
        dz = _snap(-(low[2] + high[2]) / 2)
        dy = _snap(-box["ground_y"])
        placements[name] = (dx, dy, dz)
        cursor += (high[0] - low[0]) + spacing

    # centre the whole row on the origin, which is where a viewer looks first
    if placements:
        span = max(0.0, cursor - spacing)
        shift = _snap(-span / 2)
        placements = {n: (dx + shift, dy, dz)
                      for n, (dx, dy, dz) in placements.items()}
    return placements
