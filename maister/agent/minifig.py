"""Minifigures: the one assembly the stud grid cannot see.

A minifigure is not built out of studs. The head hangs on a neck pin, the arms
clip into shoulder sockets, the legs snap onto the hip block - none of it is a
stud in a tube, so the connectivity checker finds no mating points on any of
these parts and files every one of them as UNVERIFIED. The collision checker is
no better off: the moulds interpenetrate by design, so a bounding-box test has
nothing to say about them either.

The result was a hole in validation exactly where it looked like coverage. A
correctly built minifigure came back as nine separate subassemblies; and a
figure with its head floating forty LDU above its neck came back **passing**,
along with one whose head was sunk inside its torso and one whose legs had come
off. Every arrangement of these parts was equally acceptable, which is to say
none of them was checked.

So the rule is measured rather than modelled. Every part of a minifigure sits
at a fixed offset from its torso, and 1,364 figures in the Official Model
Repository agree on what those offsets are to within a couple of LDU:

    role        dy from torso, p2..p98      lateral
    hips             +32.0 .. +32.0         0
    legs             +44.0 .. +44.0         0
    arms              +8.0 ..  +9.0         up to 15.6 sideways
    head             -27.0 .. -24.0         0
    headgear         -28.0 .. -24.0         0
    hands             posed - not checked

(LDraw's +y points down, so a negative dy is *above* the torso.)

Those numbers are the whole rule. A part within tolerance of its canonical
offset is assembled, and is bonded to the figure so the build reads as one
piece; a part outside it is misassembled, and says by how much and in which
direction. Nothing here guesses: a figure whose torso is missing is not judged
at all, because there is no anchor to measure from.
"""

import math
import re

# --------------------------------------------------------------------------
# What counts as a minifigure part
#
# Matched on the leading digits of the part number, so every printed and
# moulded variant comes along with the plain one: 3626bp01 (a printed head) is
# a head, 973p1d (a printed torso) is a torso. That is also why the map is
# keyed on the stem and not the whole name - there are thousands of torso
# prints and one torso.
# --------------------------------------------------------------------------

ROLES = {
    "3626": "head",
    "973": "torso",
    "3815": "hips",
    "3816": "leg", "3817": "leg",
    "3818": "arm", "3819": "arm",
    "3820": "hand",
    # Worn on the head. Not exhaustive and does not need to be: an unlisted hat
    # is simply not checked, which is the safe direction to be wrong in.
    "3901": "headgear",   # hair, male
    "4530": "headgear",   # hair, shoulder length
    "3833": "headgear",   # construction helmet
    "3624": "headgear",   # police hat
    "3878": "headgear",   # top hat
    "6131": "headgear",   # wizard's hat
    # Worn on the torso rather than the head, and it measures exactly there:
    # every backpack in the library sits at the torso's own origin. It was
    # listed as headgear at first and every figure carrying one was reported
    # broken - which is what checking the catalogue description rather than
    # guessing from the part number would have said in the first place.
    "2524": "backpack",
}

_STEM = re.compile(r"^(\d+)")

# dy from the torso, tolerance on it, and how far sideways the part may sit.
#
# The `dy` values are the modes - where the great majority of real parts sit,
# and what the suggested fix names. The tolerances are deliberately looser than
# the measured spread, because the two ways of being wrong here do not cost the
# same. This check exists to catch a head forty LDU above its neck; a head
# three LDU low is a real set's rounding, and telling a builder to "fix" a
# figure that is already right spends a repair round and can talk it into
# breaking one. So the band is wide enough to admit every real figure, and a
# gross error still clears it by an order of magnitude.
#
# Measured over 1,383 minifigures: with these tolerances 99.9% of real figures
# read as assembled, and the handful left are spare heads and arms lying in
# boxes rather than figures at all.
CANON = {
    "hips":     {"dy": 32.0, "dy_tol": 3.0, "lateral": 6.0},
    "leg":      {"dy": 44.0, "dy_tol": 4.0, "lateral": 8.0},
    "arm":      {"dy": 9.0, "dy_tol": 4.0, "lateral": 20.0},
    "head":     {"dy": -24.0, "dy_tol": 6.0, "lateral": 6.0},
    "headgear": {"dy": -24.0, "dy_tol": 8.0, "lateral": 8.0},
    "backpack": {"dy": 0.0, "dy_tol": 4.0, "lateral": 10.0},
}

# Hands are posed - the measured spread is -1.7..+28.7 in y and 24 LDU
# sideways, because an arm swings. They belong to the figure and are bonded to
# it, but there is no offset worth holding them to.
UNCHECKED = frozenset(("hand",))

# Roles a figure does not have to have. These are checked the way a held tool
# is: on the head, it is worn and bonded; anywhere else, it is simply not this
# figure's hat and is left exactly as the stud checker found it.
#
# The distinction is the difference between a fault and a fact. A torso with no
# legs under it is broken. A helmet on a shelf near a figure is a helmet on a
# shelf - and since headwear is matched by catalogue category, that is now
# hundreds of parts that could otherwise be claimed by a figure standing within
# reach of them and reported as its head gear, 60 LDU out of place.
OPTIONAL = frozenset(("headgear", "backpack"))

# How far from a torso a part may be and still belong to that figure. A
# minifigure is about 72 LDU from feet to hat, so this reaches the whole of one
# and stops well short of the next figure on the shelf.
CLAIM_RADIUS = 90.0

# --------------------------------------------------------------------------
# What a figure is holding
#
# A hand is a C-shaped clip and a tool is a bar pushed through it, so there is
# no single held position - the bar slides. A sword gripped at the hilt and a
# torch gripped halfway down its shaft sit at different points on the same
# line. What is fixed is the line: in the hand part's own frame, the grip axis
# runs along local y at x = 0, z = -10.5.
#
# Measured over every accessory placed near a hand in the reference library:
# 91% sit within 5 LDU of that axis, and the ones that do not are skirts, hair
# and airtanks - parts *worn* by a figure rather than held in its hand.
#
# This check only ever accepts. A sword lying on a table is a perfectly good
# model, so a part that is not on a grip axis is left exactly as the stud
# checker found it; being on one is what earns it a bond to the figure.
# --------------------------------------------------------------------------

GRIP_X, GRIP_Z = 0.0, -10.5
GRIP_RADIUS = 5.0
# How far along the axis a grip may sit. Measured -24.2 .. +12.2; a little
# wider, because the limit is the length of the bar and not a convention.
GRIP_Y = (-28.0, 16.0)
# Beyond this a part is not in the hand whatever the arithmetic says.
HAND_REACH = 26.0


def role_of(part_name):
    """The minifigure role a part plays, or None if it is not one.

    The body is matched on the part number, because those six moulds are exact
    and there is no ambiguity to resolve. What a figure *wears* is matched on
    the catalogue's own category instead - `Minifig Headwear` and `Minifig
    Neckwear` are already the distinction this needs, and there are hundreds of
    hats. Listing the six I happened to think of meant every other hat in the
    library was an unknown part hovering above a figure's head.
    """
    name = str(part_name or "").lower().rsplit("/", 1)[-1]
    if name.endswith(".dat"):
        name = name[:-4]
    stem = _STEM.match(name)
    if stem and stem.group(1) in ROLES:
        return ROLES[stem.group(1)]

    from . import catalog

    worn = catalog.worn_by_minifig(name)
    if worn == "head":
        return "headgear"
    if worn == "torso":
        return "backpack"
    return None


def _local(matrix, delta):
    """``delta`` expressed in the frame of a part with this rotation.

    The rotation is orthonormal, so its inverse is its transpose - which is
    what makes a figure lying on its back or facing away measurable with the
    same numbers as one standing up facing front.
    """
    return (matrix[0] * delta[0] + matrix[3] * delta[1] + matrix[6] * delta[2],
            matrix[1] * delta[0] + matrix[4] * delta[1] + matrix[7] * delta[2],
            matrix[2] * delta[0] + matrix[5] * delta[1] + matrix[8] * delta[2])


def _group(flat):
    """Minifigure parts, gathered around the torso each one belongs to.

    Nearest torso wins. Two figures standing shoulder to shoulder are still
    twenty LDU apart and every part of each is within a few LDU of its own
    torso's axis, so nearest-torso is not a close-run thing in practice.
    """
    torsos = [i for i, inst in enumerate(flat)
              if role_of(inst.src.part_name) == "torso"]
    if not torsos:
        return {}

    figures = {i: [] for i in torsos}
    for index, inst in enumerate(flat):
        role = role_of(inst.src.part_name)
        if role is None or role == "torso":
            continue
        best, best_d = None, CLAIM_RADIUS
        for t in torsos:
            d = math.dist(inst.pos, flat[t].pos)
            if d < best_d:
                best, best_d = t, d
        if best is not None:
            figures[best].append((index, role))
    return figures


def held_by(hand, part):
    """Is ``part`` in this hand? Returns how far along the grip, or None.

    Both arguments are flattened instances. The test is the grip axis above:
    the part's origin must lie on the line the bar runs along, and within the
    length of a bar of it.
    """
    delta = [part.pos[i] - hand.pos[i] for i in range(3)]
    if math.dist(part.pos, hand.pos) > HAND_REACH:
        return None
    dx, dy, dz = _local(hand.matrix, delta)
    if math.hypot(dx - GRIP_X, dz - GRIP_Z) > GRIP_RADIUS:
        return None
    if not GRIP_Y[0] <= dy <= GRIP_Y[1]:
        return None
    return round(dy, 1)


def _accessories(flat, hand_indices, claimed):
    """Everything the figure's hands are holding.

    Yields ``(index, hand_index, grip_y)``. ``claimed`` is every part already
    accounted for as a piece of a figure, so a hand is never found to be
    holding an arm.
    """
    for index, inst in enumerate(flat):
        if index in claimed or role_of(inst.src.part_name):
            continue
        for hand in hand_indices:
            grip = held_by(flat[hand], inst)
            if grip is not None:
                yield index, hand, grip
                break


def inspect(flat):
    """Check every minifigure in a flattened model.

    Returns ``{"figures": [...], "faults": [...], "bonds": [(i, j), ...],
    "assembled": {indices}, "holding": [...]}``:

    * ``bonds`` are index pairs to feed the connectivity graph, so a figure
      that is correctly put together counts as one piece rather than nine.
    * ``assembled`` are the parts that may stop being reported as UNVERIFIED -
      they have been verified, just not against a stud.
    * ``faults`` are parts that are not where the figure says they should be.
    * ``holding`` is what each figure has in its hands.
    """
    out = {"figures": [], "faults": [], "bonds": [], "assembled": set(),
           "holding": []}
    grouped = _group(flat)
    # Every part that is a piece of some figure, so a hand is never found to be
    # holding another figure's arm.
    claimed = {i for parts in grouped.values() for i, _ in parts} | set(grouped)

    for torso, parts in grouped.items():
        anchor = flat[torso]
        placed, wrong = [torso], []

        for index, role in parts:
            inst = flat[index]
            delta = tuple(inst.pos[k] - anchor.pos[k] for k in range(3))
            dx, dy, dz = _local(anchor.matrix, delta)

            if role in UNCHECKED:
                placed.append(index)
                continue

            spec = CANON.get(role)
            if spec is None:
                continue

            off_y = dy - spec["dy"]
            lateral = math.hypot(dx, dz)
            if abs(off_y) <= spec["dy_tol"] and lateral <= spec["lateral"]:
                placed.append(index)
                continue

            # Not where this figure wears it - so it is not this figure's, and
            # saying nothing is the correct answer.
            if role in OPTIONAL:
                continue

            wrong.append({
                "line": inst.src.line_no,
                "part": inst.src.part_name,
                "role": role,
                "position": [round(v, 2) for v in inst.pos],
                "off_by_ldu": round(abs(off_y) if abs(off_y) > spec["dy_tol"]
                                    else lateral, 2),
                "problem": _phrase(role, off_y, lateral, spec),
                "fix": _fix(role, anchor, spec),
            })

        # What its hands are holding. Bonded to the hand rather than to the
        # torso, which is both where it actually is and what keeps the bond
        # honest - an accessory reaches the rest of the figure through the hand
        # that holds it, the way it does in plastic.
        #
        # Done whether or not the figure itself is sound: a sword really is in
        # that hand, and reporting it as a loose piece on top of the fault that
        # is actually there would send the builder after the wrong thing.
        hands = [i for i, role in parts if role == "hand"]
        for index, hand, grip in _accessories(flat, hands, claimed):
            out["assembled"].add(index)
            out["bonds"].append((hand, index))
            out["holding"].append({
                "line": flat[index].src.line_no,
                "part": flat[index].src.part_name,
                "held_by_line": flat[hand].src.line_no,
                "grip_y": grip,
            })

        # The parts of the figure that are where they should be are bonded to
        # the torso and count as verified. The misplaced ones are left visibly
        # loose on purpose: bonding a broken figure would hide the fault twice
        # over, since it would then read as one connected piece *and* drop out
        # of the fragmentation report.
        out["assembled"].update(placed)
        out["bonds"].extend((torso, i) for i in placed if i != torso)
        out["faults"].extend(wrong)

        out["figures"].append({
            "torso_line": anchor.src.line_no,
            "parts": len(placed) + len(wrong),
            "assembled": not wrong,
            "holding": sum(1 for h in out["holding"]
                           if h["held_by_line"] in
                           {flat[i].src.line_no for i in hands}),
        })

    return out


def _phrase(role, off_y, lateral, spec):
    """What is wrong with this part, in the direction a builder thinks in."""
    if abs(off_y) > spec["dy_tol"]:
        # LDraw's +y is down, so a part with a positive error sits too low.
        way = "too low" if off_y > 0 else "too high"
        return (f"the {role} is {abs(off_y):.1f} LDU {way} - it is not on the "
                f"torso")
    return (f"the {role} is {lateral:.1f} LDU off the figure's centre line, "
            f"which is further sideways than it can sit")


def _fix(role, anchor, spec):
    """The coordinate that would put it right, for an axis-aligned figure.

    Withheld when the figure is rotated: the offset is still `dy` along the
    torso's own axis, but naming a world y for it would be wrong, and a
    confident wrong number is worse than none.
    """
    if anchor.matrix != [1, 0, 0, 0, 1, 0, 0, 0, 1]:
        return (f"place it {abs(spec['dy']):.0f} LDU "
                f"{'below' if spec['dy'] > 0 else 'above'} the torso, along "
                f"the axis the figure is rotated onto")
    return (f"put it at ({anchor.pos[0]:.0f}, "
            f"{anchor.pos[1] + spec['dy']:.0f}, {anchor.pos[2]:.0f})")


NOTE = (
    "A minifigure is not built on studs - the head hangs on a neck pin, the "
    "arms clip into the shoulders, the legs snap onto the hips - so these "
    "parts are checked against the offsets every real minifigure uses instead "
    "of against the stud grid. Relative to the torso: hips +32, legs +44, "
    "arms +9, head -24, and headgear with the head (LDraw's +y is down, so a "
    "negative offset is above)."
)
