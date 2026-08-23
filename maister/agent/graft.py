"""Taking a real assembly out of an official set and putting it in your build.

The reference tools already hand the builder the LDraw source of 1,800 released
sets, and the source is the good part - thirty lines of real coordinates for the
exact feature it is about to invent. What was missing was any way to *use* them.

To copy a wing out of set 10030 the builder had to read forty lines of
coordinates and type them out again with every x, y and z shifted to its own
origin. That is expensive in tokens, and it is precisely the arithmetic that
this project has spent its whole time removing from the model's hands: the same
transcription that put a canopy on a 20 LDU pitch and a chair on two lattices.
So the reference got read, admired, and then ignored, and the builder derived
the shape from first principles anyway.

This module does the transplant instead:

* **reads** the named assembly out of the set, flattened, so a submodel that
  references other submodels comes out as plain parts rather than as a
  reference to a block that does not exist in the destination;
* **re-anchors** it - the section arrives with its footprint centred on where
  you asked for it and its underside at that height, rather than at whatever
  coordinates it happened to occupy inside a 2,000-line MPD;
* **turns** it, in right angles;
* **recolours** it, because the shape is the reusable part and the set's colours
  usually are not;
* **credits** it, with a comment naming the set and the assembly, so a model
  that borrowed a wheel arch says so.

# What it does not do

It never touches the set. The corpus is read-only reference and the only file
written is the one being built.

It is also not a way to submit someone else's model as your own: the unit is an
assembly - a wing, a wheel arch, a cab roof - and the comment it leaves says
where that came from. Copying an entire set wholesale is possible and is
obviously not building anything, which is a judgement for the prompt rather than
a rule enforceable here.
"""

import sys

from . import sets
from .config import CHECKER_DIR

if str(CHECKER_DIR) not in sys.path:
    sys.path.insert(0, str(CHECKER_DIR))

import ldr_collision_checker as coll  # noqa: E402

# One assembly, not one set. Past this it is not a component being reused, it
# is somebody else's model being pasted in, and the piece counts stop meaning
# anything.
MAX_PARTS = 250

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)

_ROTATIONS = {
    0: (1, 0, 0, 0, 1, 0, 0, 0, 1),
    90: (0, 0, 1, 0, 1, 0, -1, 0, 0),
    180: (-1, 0, 0, 0, 1, 0, 0, 0, -1),
    270: (0, 0, -1, 0, 1, 0, 1, 0, 0),
}


class GraftError(ValueError):
    """The assembly could not be taken, with why."""


def _mat_mul(a, b):
    return [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
            for r in range(3) for c in range(3)]


def _mat_vec(m, v):
    return tuple(sum(m[r * 3 + c] * v[c] for c in range(3)) for r in range(3))


def _wanted(parts, only=None, exclude=None, matching=None):
    """The subset of an assembly worth taking.

    A whole assembly is often more than is wanted. Building a car, the useful
    thing in a real racer is its four wheels and its windscreen, not its
    chassis - and taking the chassis too means deleting it afterwards, line by
    line, which is the work this tool exists to avoid.

    So a graft can be narrowed three ways: to named part numbers, away from
    named part numbers, or to parts whose catalogue description contains a word
    ("wheel", "windscreen", "slope"). They combine.
    """
    def ids(value):
        if not value:
            return set()
        if isinstance(value, str):
            value = [value]
        return {str(v).strip().lower().removesuffix(".dat") for v in value if v}

    keep, drop = ids(only), ids(exclude)
    words = [w.strip().lower() for w in
             ([matching] if isinstance(matching, str) else (matching or []))
             if str(w).strip()]

    if not keep and not drop and not words:
        return parts

    from . import catalog

    chosen = []
    for entry in parts:
        name = str(entry[0]).lower().removesuffix(".dat").rsplit("/", 1)[-1]
        if drop and name in drop:
            continue
        if keep and name in keep:
            chosen.append(entry)
            continue
        if words:
            row = catalog.get_part(name) or {}
            described = str(row.get("description") or "").lower()
            if any(w in described for w in words):
                chosen.append(entry)
            continue
        if not keep:
            chosen.append(entry)
    return chosen


def extract(set_number, submodel=None, only_parts=None, exclude_parts=None,
            matching=None):
    """Every leaf part of one assembly, in that assembly's own coordinates.

    Returns ``(parts, meta)`` where each part is
    ``(part_name, colour, (x, y, z), matrix)``.

    Flattened from the chosen block rather than read as text: a real set's
    assembly routinely places other blocks of the same document, and those
    references mean nothing once the lines are somewhere else. Flattening turns
    them into the parts they stand for.
    """
    rows = sets.resolve(set_number)
    if not rows:
        raise GraftError(
            f"no official model for set '{set_number}' - find one with "
            f"search_reference(kind=\"sets\")")

    row = rows[0]
    path = sets.model_path(row)
    if not path.is_file():
        raise GraftError(f"the model file for {set_number} is missing on disk")

    model = coll.parse_ldr_file(str(path))
    if not model.blocks:
        raise GraftError(f"{set_number} has no readable blocks")

    wanted = model.main_submodel
    if submodel:
        key = coll.norm_name(str(submodel).strip())
        if key not in model.blocks:
            names = sorted(model.blocks)[:20]
            raise GraftError(
                f"{set_number} has no submodel called '{submodel}'. It has: "
                + ", ".join(names))
        wanted = key

    # flatten_model always walks from the document's main block, so the block
    # being grafted is made the main one for the length of the call. Cheaper
    # and safer than a second traversal that would have to be kept in step with
    # the checker's own.
    original = model.main_submodel
    try:
        model.main_submodel = wanted
        flat, _cycles = coll.flatten_model(model)
    finally:
        model.main_submodel = original

    parts = [(inst.src.part_name, inst.src.color, tuple(inst.pos),
              list(inst.matrix)) for inst in flat]

    whole = len(parts)
    parts = _wanted(parts, only_parts, exclude_parts, matching)
    if not parts:
        raise GraftError(
            f"nothing in '{submodel or wanted}' matched that filter - it holds "
            f"{whole} part(s). Read it with read_model to see what is in it, or "
            f"drop the filter and take the assembly whole.")

    embedded = _embedded_blocks(model, parts)
    if not parts:
        raise GraftError(
            f"'{submodel or wanted}' in {set_number} holds no parts")
    if len(parts) > MAX_PARTS:
        raise GraftError(
            f"'{submodel or wanted}' is {len(parts)} parts, over the limit of "
            f"{MAX_PARTS} for one graft. Take a smaller assembly - "
            f"get_set_details lists them with their part counts.")

    return parts, {"set_number": row.get("set_number"),
                   "set_name": row.get("set_name"),
                   "submodel": submodel or wanted,
                   "parts": len(parts),
                   "of_assembly": whole,
                   "embedded": embedded}


def _embedded_blocks(model, parts):
    """The part definitions this assembly carries inside the set's own file.

    A set does not only reference catalogue parts. Printed elements - Iron
    Man's face, a control panel, a sticker - are defined *inside* the MPD as
    their own ``0 FILE something.dat`` blocks, and `flatten_model` correctly
    leaves them as leaves rather than expanding them, because they are parts
    rather than assemblies.

    Which means a graft that copies only the placements references definitions
    that do not exist where it landed, and every one of them comes back from
    validation as a part that does not exist. So the definitions travel with
    it, recursively - a printed tile is routinely built from a sub-block of its
    own - and the grafted model is self-contained.
    """
    wanted, seen = [], set()

    def visit(name):
        key = coll.norm_name(name)
        if key in seen or key not in model.blocks:
            return
        seen.add(key)
        lines = model.block_lines.get(key)
        if lines:
            # `block_lines` holds a block's body; the parser consumes the
            # "0 FILE" that opened it. Carried without one, the definition is
            # not a block at all - it is loose lines appended to whatever came
            # before, and the part it was meant to define still does not exist.
            wanted.append((key, [f"0 FILE {key}"] + list(lines)))
        for inst in model.blocks.get(key, []):
            visit(inst.part_name)

    for name, _colour, _pos, _matrix in parts:
        visit(name)
    return wanted


def bounds(parts):
    """``((min_x, min_y, min_z), (max_x, max_y, max_z))`` over the placements.

    The origins only. A part's own volume is not measured here - this exists to
    re-anchor a section, and anchoring on where the parts *sit* is both stable
    and what someone means by "put it there".
    """
    xs = [p[2][0] for p in parts]
    ys = [p[2][1] for p in parts]
    zs = [p[2][2] for p in parts]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _rotation(degrees):
    try:
        degrees = int(round(float(degrees or 0))) % 360
    except (TypeError, ValueError):
        raise GraftError(f"`rotate` must be a number of degrees, not {degrees!r}")
    if degrees % 90:
        raise GraftError(
            f"`rotate` must be a multiple of 90; {degrees} would take every "
            f"part of the assembly off the stud grid")
    return degrees, _ROTATIONS[degrees]


def _recoloured(colour, recolour):
    if recolour is None:
        return colour
    if isinstance(recolour, dict):
        # keys arrive as strings through JSON
        for key in (str(colour), colour):
            if key in recolour:
                return int(recolour[key])
        return colour
    # A single code repaints the whole assembly, except colour 16 which means
    # "inherit" and is the one value that must be left to resolve on its own.
    try:
        return colour if str(colour) == "16" else int(recolour)
    except (TypeError, ValueError):
        raise GraftError(f"`recolour` must be a colour code or a map, not {recolour!r}")


def _align(delta, parts, target_phase):
    """Nudge a translation so the assembly lands on the destination's lattice.

    Two things have to stay true at once. The assembly's *internal* geometry is
    a real set's and must not be disturbed by a single LDU - half-stud offsets
    inside it are deliberate, built on jumpers, and shifting one part relative
    to another would break a design that is known to work. And the assembly as
    a whole has to sit on the grid the destination model already uses.

    So the whole thing moves together, and the amount it moves is rounded until
    the *body* is in phase. Anchoring on a bounding-box centre lands on a half
    LDU as often as not - a 112-wide section centred between two parts - and
    that fraction is what would otherwise put every grafted part off the grid.
    """
    from . import lattice

    here = lattice.dominant([(name, position[0] + delta[0],
                              position[2] + delta[2], matrix)
                             for name, _colour, position, matrix in parts])
    if here is None:
        # Nothing measurable to align - snap to the stud grid and take it.
        return (round(delta[0] / 10.0) * 10.0,
                round(delta[1] / 4.0) * 4.0,
                round(delta[2] / 10.0) * 10.0)

    goal = target_phase or (0.0, 0.0)
    return (delta[0] + lattice.correction(here[0], goal[0]),
            # Heights are plates and bricks, never fractions of one.
            round(delta[1] / 4.0) * 4.0,
            delta[2] + lattice.correction(here[1], goal[1]))


def place(parts, at, rotate=0, recolour=None, anchor="bottom-centre",
          target_phase=None):
    """The assembly's lines, moved so it sits at ``at``. Returns ``(lines, meta)``.

    ``at`` is where the section goes, not where its original origin goes: a
    block lifted out of a 2,000-line file has coordinates that mean nothing in
    the destination, so it is re-anchored. By default the anchor is the middle
    of its footprint and its underside - the point you would put your finger on
    to set it down.
    """
    if not isinstance(at, (list, tuple)) or len(at) != 3:
        raise GraftError(f"`at` must be [x, y, z] in LDU; got {at!r}")
    try:
        at = [float(v) for v in at]
    except (TypeError, ValueError):
        raise GraftError(f"`at` must be three numbers; got {at!r}")

    degrees, matrix = _rotation(rotate)

    # Turn about the assembly's own centre, then measure, so a rotated section
    # is anchored by where it ends up rather than where it started.
    turned = []
    for name, colour, position, part_matrix in parts:
        turned.append((name, colour, _mat_vec(matrix, position),
                       _mat_mul(matrix, part_matrix)))

    (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds(turned)
    if anchor == "origin":
        base = (0.0, 0.0, 0.0)
    elif anchor == "centre":
        base = ((min_x + max_x) / 2, (min_y + max_y) / 2, (min_z + max_z) / 2)
    else:
        # -Y is up, so the underside of a build is its *largest* y.
        base = ((min_x + max_x) / 2, max_y, (min_z + max_z) / 2)

    delta = _align((at[0] - base[0], at[1] - base[1], at[2] - base[2]),
                   turned, target_phase)

    def number(value):
        rounded = round(float(value), 3)
        return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"

    lines = []
    for name, colour, position, part_matrix in turned:
        moved = [position[i] + delta[i] for i in range(3)]
        values = " ".join(number(v) for v in part_matrix)
        lines.append(
            f"1 {_recoloured(colour, recolour)} "
            f"{' '.join(number(v) for v in moved)} {values} {name}")

    return lines, {
        "parts": len(lines),
        "rotated": f"{degrees}° about y" if degrees else "none",
        "anchor": anchor,
        "footprint_ldu": {"x": round(max_x - min_x, 1),
                          "z": round(max_z - min_z, 1)},
        "height_ldu": round(max_y - min_y, 1),
    }


def credit(meta):
    """The comment that says where an assembly came from."""
    where = f"set {meta.get('set_number')}"
    if meta.get("set_name"):
        where += f" “{meta['set_name']}”"
    block = meta.get("submodel")
    if block and not str(block).startswith("__"):
        where += f", submodel {block}"
    return f"0 // {meta.get('parts')} parts grafted from {where}"
