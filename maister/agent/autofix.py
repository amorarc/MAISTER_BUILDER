"""Moving a part out of another one, when a legal move exists.

Most overlaps the checker reports are near misses: a brick one stud too far
east, a plate sunk one plate too low, a tile put down a stud away from where it
was meant to go. Every one of those has an obvious repair — slide the part one
position along the grid — and none of them is worth a round trip to the model,
which answers slowly, sometimes gets the arithmetic wrong, and has to be shown
the file again afterwards to find out whether it worked.

So the repair is tried here first. Every move considered lands back on the stud
grid — whole studs sideways, whole plates vertically, and half a stud only for
the round 1x1 elements that legally sit half off it — and a move is accepted
only when the part it moves comes out with no overlap of its own, creates none
for anybody else, and is still touching the build rather than hanging in the
air. Anything that fails those tests is left exactly as it was and handed back
for the model to think about, which is the case where thinking is actually
needed: a part with nowhere near to go is a part whose design is wrong, not
whose coordinates are.

Only translations are ever applied, which is what makes this safe to run behind
`validate_model`: the file keeps every line it had, at the number it had, so an
edit the model planned against the numbering it was last shown still lands where
it meant it to.

One overlap is never repaired here, however easy the move looks: one between
parts in two different submodels. A submodel is a sub-assembly that is correct
in itself — its parts are placed relative to each other, and it is the whole
thing that has been put in the wrong place. Nudging one brick of it clear of
the neighbour it has been driven into does not move the sub-assembly, it takes
a piece off it, and the next brick along is still buried. Run on an official
set modelled with its head intersecting its body, the old behaviour pulled the
head apart a brick at a time — sixteen of them, each move locally legal and
the sculpture ruined. So a cross-submodel overlap is reported instead, saying
which two assemblies are in each other and that the fix is where one of them
sits.
"""

from pathlib import Path

from . import catalog, collisions
from .validation import coll

# What a move may be made of. Sideways it is whole studs; vertically whole
# plates, and a brick, which is the height most things are actually out by.
STEPS_XZ = (catalog.STUD_PITCH, 2 * catalog.STUD_PITCH)
STEPS_Y = (8.0, 16.0, 24.0)

# A 1x1 round element clutches at the centre of a 2x2 cell of studs, so for
# those — and only those — half a stud is a legal place to be.
HALF_STUD = catalog.STUD_PITCH / 2.0

# Moves are compared in steps rather than in LDU, because a step is what a move
# means and LDU are only what it measures: a plate is 8 and a stud is 20, so
# anything counted in LDU thinks lifting a part onto the row above is less than
# half the intervention of sliding it along the row it is on. It is more. A
# vertical step is charged accordingly — a part slid along usually restores what
# was intended, where a part lifted onto its neighbour builds something else.
Y_STEP_COST = 1.5

# How far from where it was a part may be put: two studs along, a brick up or
# down. That is the whole of "it was nearly right" — past it, the placement is a
# decision rather than a slip, and decisions are not this function's to make.
MAX_SIDEWAYS_LDU = 2 * catalog.STUD_PITCH
MAX_VERTICAL_LDU = 24.0

# Boxes this close are touching. A part touching nothing after its move is
# floating, which is not a repair.
TOUCH_GAP = 0.6

# What a move is charged for leaving a part at a height that is not a whole
# number of plates. Larger than any move worth making, so a part standing at an
# odd height is always straightened rather than shuffled along still odd —
# trading an overlap for a misalignment fixes nothing.
OFF_HEIGHT_PENALTY = 200.0

# How many defects one call works through. A model with more overlaps than this
# is not a model with a few slips in it.
MAX_ROUNDS = 24


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _inverse3(m):
    """Inverse of a row-major 3x3, or None if it has none."""
    a, b, c, d, e, f, g, h, i = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-9:
        return None
    return [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
            (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
            (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]


def _local_delta(inst, delta):
    """A world move, expressed in the frame the part's own line is written in.

    A flattened instance carries the product of every matrix above it and its
    own. Dividing its own back out leaves the parent frame, and inverting that
    turns a move in world space into the numbers to add to the line. For a part
    placed directly in the main model both steps are the identity.
    """
    own = _inverse3(inst.src.matrix)
    if own is None:
        return None
    parent = coll.mat_mul(inst.matrix, own)
    inverse = _inverse3(parent)
    if inverse is None:
        return None
    return coll.mat_vec_mul(inverse, delta)


def _shift(box, delta):
    (minx, miny, minz), (maxx, maxy, maxz) = box
    dx, dy, dz = delta
    return ((minx + dx, miny + dy, minz + dz),
            (maxx + dx, maxy + dy, maxz + dz))


def _moved_instance(inst, delta):
    """The same placement, shifted. For measuring a move before taking it.

    A box can be shifted on its own, and for a box test that was enough. A
    measurement of shared plastic works off the placement — the part, its
    matrix and where it sits — so a candidate move has to be expressed as a
    placement too, or the measurement would keep answering about where the
    part still is.
    """
    import dataclasses

    dx, dy, dz = delta
    x, y, z = inst.pos
    return dataclasses.replace(inst, pos=(x + dx, y + dy, z + dz))


def _gap(box_a, box_b):
    """The widest separation between two boxes; 0 when they overlap."""
    return max(max(box_b[0][axis] - box_a[1][axis],
                   box_a[0][axis] - box_b[1][axis], 0.0)
               for axis in range(3))


# --------------------------------------------------------------------------
# What is wrong, and what would put it right
# --------------------------------------------------------------------------

def _defects(flat, boxes, solid, measure=None):
    """Every real overlap in the scene, worst first, as index pairs.

    ``measure`` is the shared-plastic measurement the report uses. It is passed
    through so that what this repairs and what `validate_model` complains about
    are the same set — an autofix working from the old box reading would chase
    overlaps the report no longer names, and leave the ones it does.
    "unchecked" is not a defect here: nothing can be measured, so nothing can
    be verified to have been improved by moving it.
    """
    pairs = list(zip(flat, boxes))
    where = {id(inst): i for i, inst in enumerate(flat)}
    found = []
    for inst_a, inst_b, overlap in coll.find_collisions(pairs,
                                                        same_space_only=False):
        i, j = where[id(inst_a)], where[id(inst_b)]
        kind, detail = collisions.classify_pair(inst_a, inst_b, boxes[i],
                                                boxes[j], overlap, solid,
                                                measure=measure)
        if kind is not None and kind != "unchecked":
            found.append((i, j, kind, detail))
    found.sort(key=lambda d: (-d[3]["fraction"], -d[3]["depth_ldu"]))
    return found


def _still_overlaps(index, flat, boxes, solid, box, measure=None, moved=None):
    """Whether the part at `index`, placed at `box`, is inside anything.

    ``moved`` is the instance as it would be after the move — the boxes are
    already the moved ones, but a measurement works off the placement rather
    than off a box, so it needs the part in its new position to say anything
    truthful about it.
    """
    subject = moved if moved is not None else flat[index]
    for j, other in enumerate(boxes):
        if j == index:
            continue
        overlap = coll.aabb_overlap(box, other)
        if overlap is None:
            continue
        kind, _ = collisions.classify_pair(subject, flat[j], box, other,
                                           overlap, solid, measure=measure)
        if kind is not None and kind != "unchecked":
            return True
    return False


def _touches(index, boxes, box):
    """Whether the moved part still has a neighbour to be attached to."""
    if len(boxes) < 2:
        return True
    return any(_gap(box, other) <= TOUCH_GAP
               for j, other in enumerate(boxes) if j != index)


def _snap(value, modulus):
    """How far `value` is from the nearest multiple of `modulus`, negated."""
    r = value % modulus
    return -(r - modulus if r > modulus / 2 else r)


def _upright(inst, tolerance=1e-3):
    """Whether this part's own up axis is still the model's up axis.

    A part's local +Y lands on the middle column of its matrix, so the centre
    entry is ±1 exactly when the part is standing the way it was drawn. That is
    what makes "a whole number of plates up" mean anything: the plate lattice
    runs along world Y only for parts that are upright in it.
    """
    return abs(abs(inst.matrix[4]) - 1.0) <= tolerance


def _alignment(box, partner_box, inst, partner_inst):
    """The height correction that puts this part a whole number of plates away.

    Heights are whole plates in every build there is, so a part at an odd
    height is a part in the wrong place, and a move in whole plates would leave
    it just as odd — the correction has to be folded into the move itself.

    Only for parts that are upright, though, and measured against a partner
    that is too. Turn a sub-assembly on its side — a shop sign facing the
    street, a hull built lying down — and world Y is one of its *horizontal*
    axes, where the spacing is a stud and not a plate. Snapping to 8 there does
    not straighten anything; it slides the part half a plate off the lattice it
    was actually on, and the search then prefers that move because straightened
    moves outrank plain ones. So when either part is lying over, there is no
    height to correct and none is applied.

    Sideways there is no such correction, deliberately. A part between stud
    positions is off the lattice on purpose about as often as by accident — a
    jumper offset, a SNOT panel, two plates making a puppy's eyes — and nothing
    visible from here tells the two apart. So a part that sits between studs
    keeps sitting between them, and is moved in whole studs from wherever it is:
    a real move, and one that cannot silently redesign somebody's model.
    """
    if not (_upright(inst) and _upright(partner_inst)):
        return (0.0, 0.0, 0.0)
    return (0.0, _snap(box[0][1] - partner_box[0][1], 8.0), 0.0)


def _cost(move, step_height=8.0):
    """What a move costs, in steps.

    A step sideways is a stud. A step up or down is the moved part's own
    height — a brick moved by a brick has gone one place, and calling that
    three steps because a plate happens to be a third of it would have the
    search shove bricks across the model to avoid restacking them by one.
    """
    return ((abs(move[0]) + abs(move[2])) / catalog.STUD_PITCH
            + Y_STEP_COST * abs(move[1]) / step_height)


def _reachable(move):
    return (abs(move[0]) <= MAX_SIDEWAYS_LDU + 1e-6
            and abs(move[2]) <= MAX_SIDEWAYS_LDU + 1e-6
            and abs(move[1]) <= MAX_VERTICAL_LDU + 1e-6)


def _candidates(suggested, round_element, align, step_height=8.0):
    """Legal moves, the least disruptive first."""
    pitch = catalog.STUD_PITCH
    steps = [(0.0, 0.0, 0.0)]
    for step in STEPS_XZ:
        steps += [(step, 0.0, 0.0), (-step, 0.0, 0.0),
                  (0.0, 0.0, step), (0.0, 0.0, -step)]
    for step in STEPS_Y:
        steps += [(0.0, step, 0.0), (0.0, -step, 0.0)]
    steps += [(sx * pitch, 0.0, sz * pitch) for sx in (1, -1) for sz in (1, -1)]
    if round_element:
        steps += [(sx * HALF_STUD, 0.0, sz * HALF_STUD)
                  for sx in (1, 0, -1) for sz in (1, 0, -1) if sx or sz]
    if suggested:
        axis, ldu = suggested
        steps.append(tuple(float(ldu) if a == axis else 0.0 for a in "xyz"))

    crooked = any(abs(v) > 1e-6 for v in align)
    pool = {}
    for base, penalty in ((align, 0.0),
                          ((0.0, 0.0, 0.0), OFF_HEIGHT_PENALTY if crooked else 0.0)):
        for step in steps:
            move = tuple(base[k] + step[k] for k in range(3))
            if move == (0.0, 0.0, 0.0) or not _reachable(move):
                continue
            price = _cost(move, step_height) + penalty
            if price < pool.get(move, float("inf")):
                pool[move] = price
    return sorted(pool, key=pool.get)


def _step_height(part_name):
    """One vertical place for this part: a brick's worth, or a plate's."""
    described = catalog.get_part((part_name or "").removesuffix(".dat"))
    height = (described or {}).get("place_height_ldu")
    if not isinstance(height, (int, float)) or height <= 0:
        return 8.0
    return min(max(float(height), 8.0), 24.0)


def _search(index, partner, flat, boxes, solid, suggested, round_element,
            measure=None):
    """The smallest legal move that gets this part clear, or None.

    Nothing is refused on suspicion. If a move exists that leaves the part
    clear of everything and still attached to the build, it is a fix and it
    gets made; only a part with no such move is handed back to be thought
    about. What keeps that safe is not caution about which parts to touch, it
    is that every candidate is a legal move to begin with and every one is
    verified against the whole scene before it is taken.
    """
    original = boxes[index]
    align = _alignment(original, boxes[partner], flat[index], flat[partner])
    step_height = _step_height(flat[index].src.part_name)
    for delta in _candidates(suggested, round_element, align, step_height):
        moved = _shift(original, delta)
        if _still_overlaps(index, flat, boxes, solid, moved, measure=measure,
                           moved=_moved_instance(flat[index], delta)):
            continue
        if not _touches(index, boxes, moved):
            continue
        return delta
    return None


def _round_elements(model, library_root, flat):
    """Which of these parts are 1x1 round elements.

    Imported here rather than at module scope: the connectivity checker is only
    importable once the checker directory is on the path, which importing
    `validation` above is what arranges.
    """
    import ldr_connectivity_checker as conn

    cache, bbox_cache, out = {}, {}, {}
    for inst in flat:
        name = inst.src.part_name
        if name not in out:
            try:
                out[name] = conn.part_is_round(name, library_root, cache,
                                               bbox_cache, model)
            except Exception:
                out[name] = False
    return out


def _once_only(flat):
    """Which instances can be moved by rewriting a single line.

    A line inside a submodel the model places twice describes both copies at
    once, so moving it moves the other one too. That is not a repair anybody
    asked for, so those are left alone.
    """
    seen = {}
    for inst in flat:
        seen[id(inst.src)] = seen.get(id(inst.src), 0) + 1
    return {i: seen[id(inst.src)] == 1 for i, inst in enumerate(flat)}


def _summary(inst):
    name = (inst.src.part_name or "").removesuffix(".dat")
    described = catalog.get_part(name)
    return {"line": inst.src.line_no, "part": inst.src.part_name,
            "description": (described or {}).get("description"),
            # which sub-assembly the line lives in — the thing to move when two
            # of them are inside each other
            "submodel": getattr(inst.src, "submodel", None)}


# --------------------------------------------------------------------------
# Planning and applying
# --------------------------------------------------------------------------

def plan(path, library_root=None):
    """Work out which overlaps a move would clear, without touching the file.

    Returns ``(moves, fixed, unfixed, not_reached, error)``. ``moves`` maps a
    source line number to the translation to add to that line, already
    expressed in the frame the line is written in. ``not_reached`` is how many
    overlaps were still there when the round limit ran out — nought almost
    always, and the difference between "there is nothing left" and "I stopped
    looking" whenever it is not.
    """
    from .library import ensure_library_root

    if library_root is None:
        root = ensure_library_root()
        library_root = str(root) if root else None

    try:
        model = coll.parse_ldr_file(str(path))
    except OSError as e:
        return {}, [], [], 0, f"could not read {path}: {e}"

    flat, boxes, solid = collisions.build_scene(coll, model, library_root)
    if not flat:
        return {}, [], [], 0, "there are no parts in this file"

    # The same measurement the report uses, built once for this file. What is
    # repaired here and what `validate_model` complains about have to be one
    # set; see `_defects`.
    measure = collisions.measurer(coll, library_root, model)

    round_by_name = _round_elements(model, library_root, flat)
    movable = _once_only(flat)

    moves, fixed, unfixed = {}, [], []
    given_up = set()

    for _ in range(MAX_ROUNDS):
        remaining = [d for d in _defects(flat, boxes, solid, measure)
                     if (d[0], d[1]) not in given_up]
        if not remaining:
            break
        i, j, kind, detail = remaining[0]

        # Two sub-assemblies inside each other. Neither part is in the wrong
        # place relative to its own siblings, so neither is this function's to
        # move — see the note at the top of the file.
        across = (getattr(flat[i].src, "submodel", None)
                  != getattr(flat[j].src, "submodel", None))

        # Try the part placed later first: it is the one the fix advice names,
        # and the one more likely to be the mistake.
        order = [j, i] if flat[j].src.line_no >= flat[i].src.line_no else [i, j]
        applied = False
        # Why nothing was moved, when nothing is: a line the model places more
        # than once and a line with nowhere to go are both refusals, and saying
        # the second when it was the first sends the reader looking for room
        # that was never the problem.
        reused = tried = 0

        for index in ([] if across else order):
            if kind == "duplicate":
                continue
            if not movable.get(index):
                reused += 1
                continue
            tried += 1
            # The advice says which way to push the second part; pushing the
            # first one the same way would only drive it further in.
            suggested = ((detail["suggested_move"]["axis"],
                          detail["suggested_move"]["ldu"])
                         if index == j else None)
            delta = _search(index, i if index == j else j, flat, boxes, solid,
                            suggested,
                            round_by_name.get(flat[index].src.part_name))
            if delta is None:
                continue

            local = _local_delta(flat[index], delta)
            if local is None:
                continue

            line_no = flat[index].src.line_no
            before = moves.get(line_no, (0.0, 0.0, 0.0))
            moves[line_no] = tuple(before[k] + local[k] for k in range(3))
            boxes[index] = _shift(boxes[index], delta)
            fixed.append({
                **_summary(flat[index]),
                "moved_ldu": {"x": round(delta[0], 2), "y": round(delta[1], 2),
                              "z": round(delta[2], 2)},
                "was_inside_line": flat[j if index == i else i].src.line_no,
                "kind": kind,
                "depth_ldu": detail["depth_ldu"],
            })
            applied = True
            break

        if applied:
            continue

        given_up.add((i, j))
        if across:
            here = getattr(flat[i].src, "submodel", None) or "the main model"
            there = getattr(flat[j].src, "submodel", None) or "the main model"
            why = (f"this overlap runs between two sub-assemblies, `{here}` "
                   f"and `{there}`. Taking one part out of a sub-assembly "
                   f"pulls it apart without moving it anywhere, so it is not "
                   f"done for you: either place the whole sub-assembly "
                   f"somewhere else, or change the part that was put in "
                   f"its way")
        elif kind == "duplicate":
            why = ("the same part is placed twice in one spot — one of the two "
                   "lines has to go, and only you can say which")
        elif reused and not tried:
            why = ("this line is inside a submodel that is placed more than "
                   "once, so moving it would move every copy. Change where the "
                   "submodel is placed, or give the copy that is wrong a "
                   "submodel of its own")
        else:
            why = ("no move of a stud or two leaves this part clear and still "
                   "attached — it needs a different place in the build, a "
                   "different part, or deleting")
        unfixed.append({
            "a": _summary(flat[i]), "b": _summary(flat[j]),
            "kind": kind,
            "depth_ldu": detail["depth_ldu"],
            "shared_volume_pct": detail["shared_volume_pct"],
            "fix": why,
        })

    # The loop above is capped, and a report that stopped early must not read
    # as "all clear": a caller told nothing is left will not look again. So
    # whatever is still overlapping when the rounds run out is counted, and
    # counted separately from the overlaps that were looked at and refused.
    not_reached = len([d for d in _defects(flat, boxes, solid, measure)
                       if (d[0], d[1]) not in given_up])

    return moves, fixed, unfixed, not_reached, None


def _num(value):
    """An LDraw coordinate, written the way a hand would write it."""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _rewrite(line, delta):
    """One type-1 line with its translation moved by `delta`, or None."""
    tokens = line.split()
    if len(tokens) < 15 or tokens[0] != "1":
        return None, None
    try:
        xyz = [float(tokens[2]), float(tokens[3]), float(tokens[4])]
    except ValueError:
        return None, None
    moved = [v + d for v, d in zip(xyz, delta)]
    indent = line[:len(line) - len(line.lstrip())]
    tokens[2:5] = [_num(v) for v in moved]
    return indent + " ".join(tokens), moved


# A model where the minority lattice is this much of the build is not a model
# with a slip in it. Two nearly-equal halves means it was built two different
# ways and there is no majority to join — which is a decision, not arithmetic,
# and it goes to the builder rather than being guessed at here.
MAX_MINORITY = 0.5


def snap_lattice(path):
    """Move parts standing half a stud out of phase onto the model's own grid.

    The other half of what `fix` does for overlaps. A part whose studs sit half
    a stud off cannot connect to anything, and putting it right is a rigid
    translation of ±10 LDU — arithmetic, with exactly one answer, which is the
    definition of what this module exists to spare the builder.

    Only ever moves the minority. The model's lattice is whatever most of it
    already stands on, and moving *that* would be rebuilding the model rather
    than repairing it. Refuses when there is no clear majority to join, and
    when an axis is split more than two ways: neither is a slip.
    """
    from . import lattice as lattice_module

    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"could not read {path}: {exc}"}

    blocks = {ln.strip()[7:].strip().lower() for ln in text.splitlines()
              if ln.strip().lower().startswith("0 file ")}

    rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        tokens = line.split()
        if len(tokens) < 15 or tokens[0] != "1":
            continue
        part = " ".join(tokens[14:]).strip().lower()
        if part in blocks:
            continue
        try:
            values = [float(v) for v in tokens[2:14]]
        except ValueError:
            continue
        rows.append((number, part, values[0], values[2], values[3:12]))

    placements = [(p, x, z, m) for _, p, x, z, m in rows]
    found = lattice_module.survey(placements)
    if not found.get("split"):
        return {"changed": False, "moved": 0}

    judged = found["judged"] or 1
    for axis, info in found["split"].items():
        if len(info["phases"]) > 2:
            return {"changed": False, "moved": 0,
                    "note": (f"the {axis} axis is split {len(info['phases'])} "
                            f"ways, which is not a half-stud slip — the builder "
                            f"has to decide which grid this model is on")}
        if info["minority_parts"] / judged >= MAX_MINORITY:
            return {"changed": False, "moved": 0,
                    "note": (f"{info['minority_parts']} of {judged} parts are "
                            f"off the {axis} lattice, so there is no majority "
                            f"to join — the builder has to decide")}

    target_phase = lattice_module.dominant(placements)
    if target_phase is None:
        return {"changed": False, "moved": 0}

    moves, moved_parts = {}, []
    for number, part, x, z, matrix in rows:
        here = lattice_module.phase(part, x, z, matrix)
        if here is None:
            continue
        dx = lattice_module.correction(here[0], target_phase[0])
        dz = lattice_module.correction(here[1], target_phase[1])
        if dx or dz:
            moves[number] = (dx, 0.0, dz)
            moved_parts.append({"line": number, "part": part,
                                "moved": {"x": dx, "z": dz}})

    if not moves:
        return {"changed": False, "moved": 0}

    lines = text.splitlines()
    landed = 0
    for line_no, delta in sorted(moves.items()):
        if not 1 <= line_no <= len(lines):
            continue
        rewritten, _ = _rewrite(lines[line_no - 1], delta)
        if rewritten is None:
            continue
        lines[line_no - 1] = rewritten
        landed += 1

    if not landed:
        return {"changed": False, "moved": 0}

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "changed": True,
        "moved": landed,
        "of_total": judged,
        "onto_phase": {"x": target_phase[0], "z": target_phase[1]},
        "moved_parts": moved_parts[:10],
        "note": (f"{landed} part(s) were standing half a stud out of phase with "
                 f"the rest of the model, so nothing they touched could connect "
                 f"to them. They have been moved onto the lattice the majority "
                 f"of the model already uses. Line numbers did not change."),
    }


def fix(path, library_root=None):
    """Nudge every part that a legal move would get out of trouble.

    Writes the file only when something actually moved. Line numbers never
    change: a move rewrites the numbers inside a line and nothing else.
    """
    target = Path(path)
    moves, fixed, unfixed, not_reached, error = plan(target,
                                                    library_root=library_root)
    if error:
        return {"error": error}

    # `remaining` counts everything still wrong, listed or not — it is what the
    # caller reads to decide whether the model is clear.
    report = {"file": str(target), "moved": len(fixed),
              "remaining": len(unfixed) + not_reached,
              "not_reached": not_reached,
              "fixed_parts": fixed, "unfixed_parts": unfixed, "changed": False}
    if not moves:
        return report

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    landed = {}
    for line_no, delta in sorted(moves.items()):
        if not 1 <= line_no <= len(lines):
            continue
        rewritten, position = _rewrite(lines[line_no - 1], delta)
        if rewritten is None:
            continue
        lines[line_no - 1] = rewritten
        landed[line_no] = [round(v, 2) for v in position]

    if not landed:
        return report

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for entry in fixed:
        if entry["line"] in landed:
            entry["now_at"] = landed[entry["line"]]
    report["changed"] = True
    return report
