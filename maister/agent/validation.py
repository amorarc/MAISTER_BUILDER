"""
Runs the environment-feedback checkers in-process and returns a compact result.

Importing the checkers directly (rather than shelling out) keeps the part
geometry caches warm across the agent's repair iterations.
"""

import math
import sys
from functools import lru_cache

from . import catalog, collisions, lattice, minifig, style
from .config import CHECKER_DIR
from .library import ensure_library_root

if str(CHECKER_DIR) not in sys.path:
    sys.path.insert(0, str(CHECKER_DIR))

import ldr_collision_checker as coll          # noqa: E402
import ldr_connectivity_checker as conn       # noqa: E402


def _fmt(inst):
    where = " < ".join(reversed(inst.path)) if inst.path else inst.src.submodel
    return {
        "line": inst.src.line_no,
        "part": inst.src.part_name,
        "position": [round(v, 3) for v in inst.pos],
        "submodel": where or None,
    }


@lru_cache(maxsize=1)
def _catalog_ids():
    """Every part_id in the catalogue, lowercased, for existence checks."""
    return {(row.get("part_id") or "").strip().lower()
            for row in catalog.load_catalog()} - {""}


def _missing_parts(model, library, max_listed):
    """
    Part references that name nothing that exists.

    A type-1 line may point at three legitimate things: a block defined inside
    this document (a submodel, or an embedded "0 FILE x.dat" part), a file in
    the LDraw library on disk, or a part_id in the catalogue. Anything else is
    a part the agent invented - the file looks fine and renders as a hole.
    """
    ids = _catalog_ids()
    if not library and not ids:
        return None  # nothing to check against; don't fail on missing data

    blocks = set(model.blocks)
    found = {}
    for inst in model.instances:
        key = coll.norm_name(inst.part_name)
        if key in found:
            continue
        if key in blocks:
            continue
        if library and coll.find_part_file(inst.part_name, library):
            continue
        base = key.rsplit("/", 1)[-1]
        base = base[:-4] if base.endswith(".dat") else base
        found[key] = None if base in ids else inst

    missing = {}
    for inst in model.instances:
        key = coll.norm_name(inst.part_name)
        if found.get(key) is None:
            continue
        entry = missing.setdefault(inst.part_name, {"part": inst.part_name,
                                                    "references": 0,
                                                    "lines": []})
        entry["references"] += 1
        if len(entry["lines"]) < 10:
            entry["lines"].append(inst.line_no)

    return sorted(missing.values(), key=lambda e: e["lines"][0])[:max_listed]


def _ungoverned(flat):
    """Indices of parts the stud grid has no jurisdiction over.

    A Technic pin in its hole, an axle through a beam, a pane of glass dropped
    into a window frame, a bar in a clip, a traction band on a wheel: none of
    these ever seats on a stud, so the connectivity checker's near-miss test -
    "your underside came within a stud pitch of a stud and missed" - is asking
    a question they cannot answer. It measures them against a lattice they were
    never on and reports the distance as a fault.

    That is not a hypothetical. Across the reference corpus it was the single
    biggest source of false alarms, and the models it failed were real sets.

    Deciding it here rather than in the checker is deliberate, and it is the
    arrangement `minifig.py` already uses: the checker stays a generic piece of
    geometry that knows about studs and nothing else, and what a *part* is
    comes from the catalogue, in this layer. See catalog.seats_on_studs - a
    part the catalogue does not have comes back None and is judged as before.
    """
    out = set()
    for i, inst in enumerate(flat):
        if catalog.seats_on_studs(inst.src.part_name) is False:
            out.add(i)
    return out


# Two parts are touching when their boxes meet in all three axes, give or take
# this. Parts that are joined normally *overlap* - a stud is 4 LDU tall and
# sits inside the part above it - so this only has to cover the ones that just
# meet, plus the rounding in a tyre or a rim.
CONTACT_GAP = 1.0
# How deep the ground is: a part counts as standing on it when its underside is
# within this of the model's lowest surface. Two bricks, and the number came
# from sweeping it over 150 real sets:
#
#     24 LDU   6/150 sets   363 parts flagged
#     48 LDU   5/150 sets    58
#     72 LDU   4/150 sets    54
#     96 LDU   3/150 sets    53
#
# It has to be wide because a *scene* has no single ground - 6346 Shuttle
# Launching Crew stands its cars, its trailer and its shuttle on ground planes
# 60 LDU apart, and a narrow band called 305 of its 456 parts flying. Past 48
# there is almost nothing left to buy, and every LDU of it is sensitivity given
# away: a part this reports is one with clear air under it and nothing touching
# it anywhere, which at two bricks up is already a real fault.
GROUND_BAND = 48.0
# The cell parts are bucketed into to find what they touch. One brick.
CONTACT_CELL = 24.0


def _touching(a, b):
    """Whether two world boxes meet in all three axes, give or take CONTACT_GAP."""
    return all(min(a[1][k], b[1][k]) - max(a[0][k], b[0][k]) >= -CONTACT_GAP
               for k in (0, 1, 2))


def _contact_graph(boxes):
    """Which parts touch which: ``{index: {index, ...}}`` over ``boxes``.

    **Contact, not the stud graph**, and that choice is measured rather than
    aesthetic. Running reachability over the connectivity graph reported a
    floating part in 94.7% of real sets - a third of every part in them. That
    graph holds only the matings the stud checker can find, and a great many
    real joins leave no edge in it: a clip, a pin, a bracket, a hinge, a stud on
    the side of a brick. Whole assemblies of a real set are then their own
    island. Parts that are joined *touch*, whatever holds them, and touching is
    measurable.

    Built once per validation and used twice - by ``_floating``, which spreads
    support up from the ground along it, and by ``_disconnected``, which asks
    whether each object is one piece. Both questions are reachability over this
    same relation, and building it twice was the only thing keeping them apart.

    Bucketed into cells one brick across, so each part is only tested against
    the handful sharing its neighbourhood rather than against all n of them.
    """
    cells = {}
    for i, box in enumerate(boxes):
        for cx in range(int(box[0][0] // CONTACT_CELL), int(box[1][0] // CONTACT_CELL) + 1):
            for cy in range(int(box[0][1] // CONTACT_CELL), int(box[1][1] // CONTACT_CELL) + 1):
                for cz in range(int(box[0][2] // CONTACT_CELL), int(box[1][2] // CONTACT_CELL) + 1):
                    cells.setdefault((cx, cy, cz), []).append(i)

    joined = {}
    for group in cells.values():
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                i, j = group[a], group[b]
                if _touching(boxes[i], boxes[j]):
                    joined.setdefault(i, set()).add(j)
                    joined.setdefault(j, set()).add(i)
    return joined


def _floating(flat, boxes, joined, excused):
    """Parts held up by nothing - the model, minus everything the ground holds.

    The rule is the one asked for: what the ground holds up holds up whatever
    is joined to it, outwards until it stops, and anything the spread never
    reaches is flying. Two things about it were decided by measurement against
    the 1,819 real sets in the corpus, because both plausible readings of it
    fail badly and neither failure is visible on a small model.

    **What the support spreads along is contact, not the stud graph** - see
    ``_contact_graph``, which is where that measurement is recorded and where
    ``joined`` comes from.

    **The ground is a band, not a height.** Taking the model's lowest point and
    calling only that "the ground" fails on any set whose lowest point is a
    tyre: the baseplate is then a plate above the ground, nothing is on the
    ground, and the whole set comes back flying by 8 LDU.

    ``excused`` are parts the stud grid has no jurisdiction over - a
    minifigure's arm, a bar in a clip. They are not reported, but they still
    carry support to whatever touches them.
    """
    if not flat or not boxes:
        return []

    # -Y is up, so a part's underside is its LARGEST y. Getting that backwards
    # reads as correct either way round, and is wrong in silence.
    floor = max(box[1][1] for box in boxes)

    queue = [i for i, box in enumerate(boxes) if box[1][1] >= floor - GROUND_BAND]
    held = set(queue)
    while queue:
        for other in joined.get(queue.pop(), ()):
            if other not in held:
                held.add(other)
                queue.append(other)

    return [(i, round(floor - boxes[i][1][1], 1))
            for i in range(len(boxes))
            if i not in held and i not in excused]


def _box_gap(a, b):
    """How far apart two world boxes are. 0 when they touch or overlap."""
    gaps = [max(0.0, max(a[0][k] - b[1][k], b[0][k] - a[1][k]))
            for k in (0, 1, 2)]
    return math.sqrt(sum(g * g for g in gaps))


def _pieces(members, joined, edges):
    """``members`` split into connected pieces, largest first.

    Over two relations at once: contact, which is what actually holds a build
    together (see ``_contact_graph``), and the stud graph, which is narrower but
    catches the few joins contact cannot see. A round brick's box is shrunk to
    its inscribed cylinder before anything is measured - that is what stops it
    colliding with its neighbours' corners - and the same shrink can open a
    hair's gap between it and a part it is genuinely seated on. The stud edge
    is still there, so the union has it either way.
    """
    index = {m: n for n, m in enumerate(members)}
    parent = list(range(len(members)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for m in members:
        for other in joined.get(m, ()):
            if other in index:
                union(index[m], index[other])
    for a, b in edges:
        if a in index and b in index:
            union(index[a], index[b])

    out = {}
    for m in members:
        out.setdefault(find(index[m]), []).append(m)
    return sorted(out.values(), key=len, reverse=True)


# What ``objects`` may say a model is. See _disconnected.
OBJECT_SCOPES = ("whole", "blocks")


def _disconnected(flat, boxes, joined, edges, excused, scope, max_listed=10):
    """Objects that came out in more than one piece.

    One subconstruction is one object, and an object is one thing: every part of
    the tree is joined to the rest of the tree. What is emphatically *not*
    required is that the tree be joined to the car. They are different objects,
    and a scene with the two of them standing a stud apart is exactly right -
    joining them would be the fault.

    **The model is split into pieces once, globally, and each object is then
    asked which pieces its parts landed in.** Doing it the other way round -
    grouping first and connecting within the group - cuts every join that runs
    through a part belonging to some other group, and a set's top-level loose
    parts are exactly that: the connective tissue that ties its submodels
    together. Measured on 250 corpus sets, that mistake alone accounted for 26
    of 70 false alarms.

    ``scope`` says what an object *is*, and it is declared by the caller rather
    than inferred, because it cannot be read off the file:

    * ``"whole"`` - the file is one object, all of it. This is a subbuild: the
      harness gave this agent one object and one file to put it in.
    * ``"blocks"`` - one object per top-level block, which is what an assembled
      scene is. Parts loose in the main model are not an object of their own;
      they are connective tissue and take part only by joining what they touch.

    Nothing can infer that from the file, and the corpus is the proof. The OMR
    sets use submodels to mean *instruction step* - ``step226.ldr``, ``Steps 91
    to 94.ldr`` - and the parts added at one step are routinely nowhere near
    each other. Twenty of those 70 false alarms were that, and no reading of the
    geometry distinguishes a block that means "an object" from a block that
    means "what you add next". The harness that wrote the file knows; the file
    does not say.

    ``excused`` parts - a minifigure's arm, a bar in a clip - are not *reported*
    as the stray, for the same reason they are excused everywhere else: the
    thing that holds them is not a stud and this checker cannot see it. They
    still carry connection to whatever touches them, so an accessory bridging
    two halves of a build keeps it one piece.
    """
    if scope not in OBJECT_SCOPES or not flat:
        return []

    # The model as it actually falls apart, once, over everything at once.
    pieces = _pieces(range(len(flat)), joined, edges)
    if len(pieces) < 2:
        return []
    piece_of = {i: n for n, piece in enumerate(pieces) for i in piece}

    if scope == "whole":
        groups = {None: list(range(len(flat)))}
    else:
        groups = {}
        for i, inst in enumerate(flat):
            if inst.path:
                groups.setdefault(inst.path[0], []).append(i)

    out = []
    for name, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_piece = {}
        for i in members:
            by_piece.setdefault(piece_of[i], []).append(i)
        if len(by_piece) < 2:
            continue

        clumps = sorted(by_piece.values(), key=len, reverse=True)
        largest = clumps[0]
        strays = sorted((i for clump in clumps[1:] for i in clump
                         if i not in excused),
                        key=lambda i: flat[i].src.line_no)
        # Everything outside the largest clump is made of parts the stud grid
        # does not govern - an accessory, a pin, a pane of glass. It is adrift,
        # but nothing here can say by how much or what it should attach to, and
        # a fault nobody can act on is worse than none.
        if not strays:
            continue

        out.append({
            "object": name or "this model",
            "pieces": len(clumps),
            "parts": len(members),
            "largest_piece": len(largest),
            "detached_parts": [
                dict(_fmt(flat[i]),
                     gap_ldu=round(min(_box_gap(boxes[i], boxes[j])
                                       for j in largest), 1))
                for i in strays[:max_listed]],
        })
    return out


def _loose_pieces(flat, boxes, comps, excused, max_listed=10):
    """Every stud-connected clump except the main body, largest first.

    ``subassemblies`` has always been a bare count, which is fine while nothing
    is held to it and useless the moment something is: a builder told its model
    is in nine pieces has nowhere to start looking. Each entry here names one
    clump - how big it is, a part and line inside it, and how far it sits from
    the main body - so the count becomes a list of moves.

    ``held_by_other_means`` marks a clump made entirely of parts the stud grid
    has no jurisdiction over: a Technic pin, a pane of glass, a minifigure's
    accessory. It is still counted, because the count is over studs and these
    genuinely have none - but a builder reading the list has to be able to tell
    "this is adrift" from "this is held by something you cannot see", which are
    the same row otherwise.
    """
    if len(comps) < 2:
        return []

    main = comps[0]
    out = []
    for clump in comps[1:max_listed + 1]:
        first = min(clump)
        out.append({
            **_fmt(flat[first]),
            "parts": len(clump),
            "gap_ldu": round(min(_box_gap(boxes[first], boxes[j])
                                 for j in main), 1),
            **({"held_by_other_means": True}
               if set(clump) <= excused else {}),
        })
    return out


def _lattice_split(model, max_listed=10):
    """Whether this model is built on more than one stud lattice."""
    placements = [(inst.part_name, inst.x, inst.z, inst.matrix)
                  for inst in model.instances
                  if coll.norm_name(inst.part_name) not in model.blocks]
    found = lattice.survey(placements)
    if not found.get("split"):
        return None

    target = lattice.dominant(placements)
    stray = []
    for inst in model.instances:
        if coll.norm_name(inst.part_name) in model.blocks:
            continue
        moved = lattice.describe(inst.part_name, inst.x, inst.z,
                                 inst.matrix, target)
        if moved:
            stray.append({"line": inst.line_no, "part": inst.part_name,
                          "position": [round(inst.x, 1), round(inst.z, 1)],
                          "move": moved})

    axes = ", ".join(
        f"{axis}: {info['minority_parts']} part(s) half a stud off"
        for axis, info in found["split"].items())
    return {
        "on_two_lattices": True,
        "parts_on_the_wrong_one": len(stray),
        "of_total_judged": found["judged"],
        "detail": axes,
        "move_these": stray[:max_listed],
        "note": ("This is the cause of the `misaligned_parts` above, and it is "
                 "one mistake rather than one per part. A part's studs sit at a "
                 "fixed offset from its origin that depends on the part - a 6x6 "
                 "plate's studs are at ±10, ±30, ±50 from its centre, a 1x1's "
                 "stud is at 0 - so two parts can both sit on multiples of 20 "
                 "and still be half a stud apart, unable to connect. Apply the "
                 "`move` against each line listed and the whole group joins the "
                 "rest of the model; the moves are the same for most of them, "
                 "so this is one correction repeated, not a redesign."),
    }


def _part_index(flat, rows, comps, assembled, excused):
    """Every part, with the verdict it was counted under and the clump it is in.

    The lists above this one are samples, capped at ``max_listed``, and the cap
    is right for their reader: a builder handed four hundred misaligned rows
    learns nothing the count did not already say, and pays for the tokens.

    The viewer has the opposite problem. It puts the verdict back onto the
    model - the parts a check counted light up where they actually are - and a
    sample there does not read as a sample. It lights a tenth of what is wrong
    and quietly says the rest is fine, which is worse than showing nothing.

    So this is complete, and it is opt-in: off for the agent, on for the app.

    ``state`` mirrors the counting rules above exactly rather than the checker's
    own verdict, or the four stats and the four sets of lit parts would disagree
    with each other on screen. A part of an assembled minifigure counts as
    connected however its studs read; a part nothing on the stud grid governs -
    a Technic pin, a pane of glass - is ``held`` and counts under none of the
    three, which is why the three do not add up to ``parts``.
    """
    clump_of = {}
    for n, clump in enumerate(comps):
        for i in clump:
            clump_of[i] = n

    out = []
    for i, (status, inst, _) in enumerate(rows):
        if status == "CONNECTED" or i in assembled:
            state = "connected"
        elif i in excused:
            state = "held"
        elif status == "MISALIGNED":
            state = "misaligned"
        else:
            state = "unverified"
        out.append({
            "line": inst.src.line_no,
            "part": inst.src.part_name,
            # World space, after submodel expansion - the frame the viewer can
            # measure its own loaded parts in, so the two can be matched up
            # without either side counting lines.
            "at": [round(v, 3) for v in inst.pos],
            "state": state,
            # Index into `comps`, largest clump first, so 0 is the main body.
            "clump": clump_of.get(i, 0),
        })
    return out


def validate(path, tolerance=2.0, max_listed=25, objects=None, index=False):
    """Run both checkers on an LDraw file. Returns a JSON-serialisable dict.

    ``objects`` says what free-standing object this file holds, so that "every
    part of one object is joined to the rest of it" can be checked. ``"whole"``
    is one object and fails the model when it comes apart; ``"blocks"`` is one
    object per top-level block and only reports. None, the default, does not
    ask at all - which is right for a file nobody has told us the shape of.
    See _disconnected for why this is declared rather than inferred.

    ``index`` adds ``connectivity.part_index``: one row per part rather than
    the capped samples. See _part_index - it is for the viewer, not the agent.
    """
    library_root = ensure_library_root()
    library = str(library_root) if library_root else None

    # The checker measures a round body off the part's own wall, which works
    # for most of them and not for the few that draw their rim as triangles.
    # The catalogue knows those by name, so it tells it. Idempotent, and the
    # set is built once - see catalog.round_bodied_parts.
    if not conn.CENTRAL_TUBE_PARTS:
        conn.CENTRAL_TUBE_PARTS.update(catalog.round_bodied_parts())

    try:
        model = coll.parse_ldr_file(str(path))
    except OSError as e:
        return {"error": f"could not read {path}: {e}"}

    if not model.instances:
        return {"error": "no part references (type-1 lines) found in the file"}

    flat, cycles = coll.flatten_model(model)

    # --- connectivity -----------------------------------------------------
    (edges, connected, near_miss, unresolved, crowded,
     seated, seat_miss, shared_studs) = conn.build_graph(
        flat, library, model, tolerance)
    rows = conn.classify(flat, connected, near_miss, seated, seat_miss)

    # --- minifigures ------------------------------------------------------
    #
    # Checked apart from everything above because they are joined by neck pins
    # and shoulder sockets rather than by studs, which is why the stud checker
    # has nothing to say about them. See minifig.py: a figure held together
    # correctly is bonded here so the model reads as one piece, and one that is
    # not says which part is where it should not be.
    figures = minifig.inspect(flat)
    if figures["bonds"]:
        edges = list(edges) + figures["bonds"]

    comps = conn.components(len(flat), edges)

    # A part verified as belonging to a figure is out of the stud checker's
    # jurisdiction entirely - in both directions.
    #
    # It is not UNVERIFIED: it was checked, against the figure rather than
    # against a stud, and leaving it here reports a correct minifigure as nine
    # unknowns.
    #
    # And it is not MISALIGNED either, which is the one that actually failed
    # models. A helmet carries something the checker reads as a stud, so a
    # helmet sitting correctly on a head comes back "off the stud grid" - it
    # is, and it is meant to be, because it is held by the head's neck pin.
    # Judging a clip against a lattice it was never on is a false alarm, and
    # this is where it gets dropped.
    # ...and the same again for every other part held by something the stud
    # checker cannot see. See _ungoverned: a Technic pin, an axle, a pane of
    # glass. Excused from both verdicts for the same reason a minifigure's arm
    # is - they were checked against the thing that actually holds them, which
    # is not a stud.
    ungoverned = _ungoverned(flat)
    excused = figures["assembled"] | ungoverned

    misaligned = [dict(_fmt(inst), gap_ldu=round(gap, 2))
                  for i, (status, inst, gap) in enumerate(rows)
                  if status == "MISALIGNED" and i not in excused]
    unverified = [_fmt(inst) for i, (status, inst, _) in enumerate(rows)
                  if status == "UNVERIFIED" and i not in excused]

    # --- part existence ---------------------------------------------------
    missing = _missing_parts(model, library, max_listed)
    # A part that does not exist has no geometry either, so it turns up in
    # `unresolved` as well. Report it once, under the name that says why.
    if missing:
        unresolved = {n for n in unresolved
                      if n not in {m["part"] for m in missing}}

    # --- collisions -------------------------------------------------------
    #
    # The scene is built once and used twice: the same world boxes answer "do
    # these two share plastic" and "is the bottom of this part on the ground".
    scene = collisions.build_scene(coll, model, library)
    # One measurer for this validation, so every pair in this model is judged
    # against solids rasterised once. See occupancy.py.
    measure = collisions.measurer(coll, library, model)
    overlaps = collisions.inspect(coll, model, library, max_listed=max_listed,
                                  scene=scene, measure=measure)

    # --- held up by nothing, and come apart --------------------------------
    #
    # Both are reachability over what touches what, so the graph is built once
    # and asked twice. See _contact_graph.
    contact = _contact_graph(scene[1])
    floating = _floating(flat, scene[1], contact, excused)
    apart = _disconnected(flat, scene[1], contact, edges, excused,
                          objects, max_listed)
    # The pieces behind the `subassemblies` count, so that a ceiling on it can
    # be acted on rather than only reported. See _loose_pieces, and
    # runstate.MAX_SUBASSEMBLIES for what reads this.
    loose = _loose_pieces(flat, scene[1], comps, excused)

    result = {
        "file": str(path),
        "library_resolved": bool(library),
        "parts": len(flat),
        "connectivity": {
            "connected": sum(1 for i, r in enumerate(rows)
                             if r[0] == "CONNECTED" or i in figures["assembled"]),
            "misaligned": len(misaligned),
            "unverified": len(unverified),
            "subassemblies": len(comps),
            **({"loose_pieces": loose,
                "loose_pieces_note": (
                    "`subassemblies` counts the clumps this model falls into "
                    "over STUD connections alone, and these are the ones that "
                    "are not the main body. It is a stricter question than "
                    "`objects_in_pieces` above, which also counts parts that "
                    "merely touch - so a build can be clean there and still be "
                    "listed here. Each entry gives a part and line inside the "
                    "clump and how far it sits from the main body: move it onto "
                    "real studs of the build, or bridge the gap with a plate "
                    "that reaches both. A clump marked "
                    "`held_by_other_means` is held by a clip, a pin or a grip "
                    "the stud checker cannot see, and is only listed because "
                    "the count includes it.")}
               if loose else {}),
            "misaligned_parts": misaligned[:max_listed],
            "unverified_parts": unverified[:max_listed],
            **({"part_index": _part_index(flat, rows, comps,
                                          figures["assembled"], excused)}
               if index else {}),
            # One object per entry, never the scene: two objects standing apart
            # is not a fault, an object in two halves is. See _disconnected.
            "objects_in_pieces": apart,
            # Which question was asked, so that an empty list above can be told
            # apart from the question never having been put. They are the same
            # value and opposite facts - "measured, and it is one piece" is
            # something the visual critic can be held to, and "nobody asked" is
            # not.
            "objects_checked": objects,
            **({"objects_in_pieces_note": (
                "Each of these is ONE object that came out as several separate "
                "clumps with nothing joining them - in real bricks it is not one "
                "model, it is a handful of loose pieces that fall apart when you "
                "pick it up. `largest_piece` is the part count of the main clump; "
                "`detached_parts` are the ones adrift from it, each with the "
                "`gap_ldu` between it and that clump. Move each one onto the "
                "build until it touches - a brick is 24 LDU tall, a plate 8, a "
                "stud 20 across - or bridge the gap with a part that reaches "
                "both. Do NOT join separate objects to each other: a scene is "
                "meant to have a tree and a car standing apart, and only the "
                "insides of each one have to hold together.")}
               if apart else {}),
            "floating": len(floating),
            "floating_parts": [
                dict(_fmt(flat[i]), height_ldu=height)
                for i, height in floating[:max_listed]],
            **({"floating_note": (
                "These are in mid-air. Nothing touches them that touches "
                "anything else that reaches the ground, so in real bricks "
                "there is nothing holding them up and they fall. "
                "`height_ldu` is how far above the model's lowest surface each "
                "one is. Bring each one down onto the build until its "
                "underside meets what should carry it - a brick is 24 LDU "
                "tall, a plate 8 - or build up to it. Moving it sideways does "
                "not help: the fault is the air underneath.")}
               if floating else {}),
            # Said out loud, because it is the half of this check that passes.
            # A Technic pin excused here and a Technic pin nobody looked at
            # produce the same silence otherwise, and only one of them means
            # the model was checked.
            "joined_by_other_means": len(ungoverned),
        },
        "collision": {
            "overlapping": overlaps["overlapping"],
            "contacts": overlaps["contacts"],
            "overlapping_parts": overlaps["overlapping_parts"],
            **{k: overlaps[k] for k in
               ("unchecked_deep_overlaps", "unchecked_overlap_parts",
                "unchecked_note") if k in overlaps},
            "note": ("`contacts` are parts touching as they should - a stud "
                     "inside the part above it - and need no action. "
                     "`overlapping_parts` are parts sharing solid plastic, "
                     "measured off the parts' real shapes and given in cubic "
                     "LDU as `shared_ldu3`: each carries the move that "
                     "resolves it. Apply that move; never invent one that "
                     "leaves the stud grid."),
        },
    }

    if missing:
        result["missing_parts"] = missing
        result["missing_parts_note"] = (
            "These names are not a submodel in this file, not a file in the "
            "LDraw library and not a part_id in the catalogue - they do not "
            "exist. Search for the part you meant with search_parts and use "
            "the part_id it returns, or define the submodel in the file.")
    # `shared_studs` - two parts seated on one stud - is deliberately NOT
    # reported, and the reason is worth keeping.
    #
    # As a rule it is exact: a stud goes into one anti-stud, so two parts on
    # one stud are in the same place, and unlike the collision check it needs
    # no opinion about a part's shape. It catches what that check cannot -
    # slopes, wedges and brackets are exempt from collision because a bounding
    # box is too poor a likeness of them, and two 2x2 slopes placed one stud
    # apart share a full stud of plastic and validate clean.
    #
    # What makes it unusable is the input, not the rule. A part's seats are
    # only partly real: where the part has no studs of its own they are
    # fabricated from its bounding box, and for anything that is not a full
    # rectangle underneath - a corner plate with three studs in four cells, an
    # L-shaped bracket spanning both arms, a train base full of holes - the
    # fabricated cells land on studs that genuinely belong to a neighbour.
    # Turned on, it failed 808 further models of the reference corpus, taking
    # it from 81.8% to 37.4%, and the pairs it named were a corner plate beside
    # a tile and a bracket beside a plate.
    #
    # It becomes sound the moment seats are real rather than inferred, which is
    # what a connection-metadata library (LDCad's shadow library) would give.
    # Until then this stays computed and unused rather than deleted, because
    # the rule is right and only the data is missing.
    if crowded:
        result["overcrowded_studs"] = [
            dict(_fmt(flat[i]),
                 covered_by=[{"line": flat[j].src.line_no,
                              "part": flat[j].src.part_name}
                             for j in others[:4]])
            for i, others in crowded[:max_listed]]
        result["overcrowded_studs_note"] = (
            "A 2x2 cell of studs carries one thing. Either a part sits on all "
            "four studs, or a round 1x1 element stands in the gap between them "
            "- never both, because the round element fills the space the part "
            "above comes down into. Five studs' worth of plastic will not go on "
            "four studs. Move the round element to a cell nothing covers, or "
            "drop the part that covers this one.")
    if figures["figures"]:
        result["minifigures"] = {
            "found": len(figures["figures"]),
            "assembled": sum(1 for f in figures["figures"] if f["assembled"]),
            "note": minifig.NOTE,
        }
        if figures["faults"]:
            result["minifigures"]["misassembled_parts"] = \
                figures["faults"][:max_listed]
        if figures["holding"]:
            # Said out loud because it is the half of this check that passes.
            # A tool in a hand is attached by a grip the stud checker cannot
            # see, so without this line the only evidence it was recognised is
            # the absence of a complaint - and the builder cannot tell that
            # apart from the check not having run.
            result["minifigures"]["held_accessories"] = \
                figures["holding"][:max_listed]

    # --- the stud lattice --------------------------------------------------
    #
    # Reported as a cause, next to the misaligned parts that are its symptom.
    # A model split across two lattices produces one "off the grid" row per
    # part on the losing side - twenty-two of them, in the build this was
    # written for - and a builder reading twenty-two rows makes twenty-two
    # edits. It is one decision, and it takes one correction applied to a
    # group. See lattice.py.
    split = _lattice_split(model)
    if split:
        result["lattice"] = split

    # --- style ------------------------------------------------------------
    #
    # Read off the source lines rather than the flattened build, and with
    # submodel references dropped, because the question is what this file's
    # author wrote - see style.py. It cannot fail a model and is left out
    # entirely when there is nothing worth remarking on.
    styled = style.report(
        [inst for inst in model.instances
         if coll.norm_name(inst.part_name) not in model.blocks],
        known=_catalog_ids())
    if styled:
        result["style"] = styled

    if cycles:
        result["circular_references"] = [
            {"line": inst.line_no, "path": list(p)} for inst, p in cycles]
    if unresolved:
        result["unresolved_parts"] = sorted(unresolved)[:max_listed]

    # An object in pieces fails the model, and this replaces the old
    # `fragmented_submodels`, which reported the same idea and deliberately
    # failed nothing. The reason it could not fail anything is worth keeping,
    # because it is *still true of the thing it measured*: it counted pieces
    # over the stud graph, and a build legitimately comes apart into several
    # sub-assemblies there - a minifig beside a vehicle, a lid that lifts off,
    # anything held by a clip, a pin or a hinge. Over that graph "more than one
    # piece" rejects models that are perfectly buildable, so it was reported and
    # ignored, which is the same as not checking.
    #
    # Two changes make it a fault worth having. It counts pieces over *contact*
    # instead, which is the relation that actually holds a build together and
    # the one the floating check was already measured on; and it asks per
    # object rather than of the file, so a scene of a tree and a car standing
    # apart is not a fault while a tree in two halves is. See _disconnected.
    #
    # Overlaps fail it too. Two parts sharing solid plastic is a model that
    # cannot be built out of real bricks, which is the one thing this whole
    # check exists to prevent; and unlike the raw collision count that used to
    # sit here, every one reported has been filtered down to parts whose boxes
    # really are their shape.
    #
    # Missing parts fail it for the same reason: a reference to a part that
    # exists nowhere is a hole in the model, and it is the failure the agent is
    # most likely to cause, by guessing a plausible-looking part number.
    faults = []
    if missing:
        # Name the numbers in the verdict itself. The verdict is the one line
        # that always gets read, and "3001x.dat does not exist" is actionable
        # where "missing_parts" only says to go looking.
        names = [m["part"] for m in missing]
        shown = ", ".join(names[:5])
        if len(names) > 5:
            shown += f", +{len(names) - 5} more"
        faults.append(f"missing_parts ({shown} - no such part)")
    if misaligned:
        faults.append("misaligned_parts (off the stud grid)")
    if floating:
        faults.append(f"floating_parts ({len(floating)} part(s) held up by "
                      f"nothing - no path of connections down to the ground)")
    # Only a declared single object fails on this. "blocks" is reported and not
    # enforced, and the asymmetry is deliberate: "this file is one object" is a
    # fact the harness knows about a file it created, while "each block is an
    # object" is an inference about authoring convention that is wrong for every
    # OMR set - see _disconnected. The assembly pass is also the weaker place to
    # enforce it, since each component was already gated as "whole" when it was
    # built, and `_assembly_guard` is what stops the pass taking one apart.
    if apart and objects == "whole":
        # Named object by object. "3 objects in pieces" sends the builder
        # looking; "the tree is in 4 pieces" is the whole of the fault.
        listed = ", ".join(f"{e['object']} in {e['pieces']} pieces"
                           for e in apart[:3])
        if len(apart) > 3:
            listed += f", +{len(apart) - 3} more"
        faults.append(f"objects_in_pieces ({listed} - the parts of one object "
                      f"are not joined to each other)")
    if figures["faults"]:
        # A minifigure whose head is not on its neck is as unbuildable as a
        # brick floating off the grid, and until this check existed it was the
        # one kind of broken model that validated perfectly.
        faults.append(f"misassembled_parts ({len(figures['faults'])} "
                      f"minifigure part(s) not where the figure holds them)")
    if crowded:
        faults.append("overcrowded_studs (a round element in a cell that "
                      "already has a part on it)")
    if overlaps["overlapping"]:
        faults.append("overlapping_parts (sharing solid plastic)")
    if cycles:
        faults.append("circular_references")
    if unresolved:
        faults.append("unresolved_parts")

    ok = not faults
    result["passed"] = ok
    result["verdict"] = (
        "PASS - every part is on the stud grid, nothing overlaps"
        + (", and it is one connected build." if objects == "whole" else ".")
        if ok else
        "FAIL - fix " + ", then ".join(faults) + "."
    )
    return result
