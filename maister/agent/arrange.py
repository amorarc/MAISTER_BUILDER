"""Moving and turning whole objects in an assembled scene.

An assembled scene is one MPD: a main model whose entire body is a handful of
type-1 lines, one per subconstruction, each naming a submodel that holds the
actual bricks. So "the tree overlaps the house" is a fault in exactly one line -
the line that places the tree - and the repair is that line's three numbers.

The assembly pass used to be handed `edit_model` for this, which is a tool for
changing lines of LDraw, and it treated the scene as what it literally is: a
file of text. That is the wrong altitude. A builder asked to separate two
buildings would read forty lines of the tree's trunk looking for the one that
places it, and having found it, would compute a rotation by hand as nine
matrix entries - where what it wanted to say was "turn the tree ninety
degrees".

These two functions are that sentence. They take the *name* of a
subconstruction and move or turn the whole of it, and everything below is the
arithmetic that saves the caller from doing it: finding the placement line,
composing rotations onto the matrix it already has, and - for a turn - putting
the object back over its own centre afterwards, because rotating a placement
about the submodel's origin swings the object across the scene, which is never
what anybody meant.
"""

import re

from .assembly import _norm

# A type-1 line, split so the numbers can be rewritten without touching the
# colour or the name: `1 <colour> <x y z> <9 matrix> <file>`.
_PLACEMENT = re.compile(
    r"^(?P<lead>\s*1\s+\S+\s+)"
    r"(?P<nums>(?:-?[\d.eE+]+\s+){12})"
    r"(?P<name>\S.*?)(?P<tail>\s*)$"
)

_FILE = re.compile(r"^\s*0\s+FILE\s+(.+?)\s*$", re.IGNORECASE)

# Turns are whole right angles. Anything else takes every stud in the object
# off the grid at once, and a scene is arranged out of things that still have
# to be buildable.
QUARTER = 90


def _num(value):
    """A coordinate written the way a hand would write it."""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _mat_mul(a, b):
    """Row-major 3x3 product."""
    return [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
            for r in range(3) for c in range(3)]


def _mat_vec(m, v):
    return [m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
            m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
            m[6] * v[0] + m[7] * v[1] + m[8] * v[2]]


def _rotation(axis, degrees):
    """A right-angle rotation about one axis, as a row-major 3x3."""
    turns = int(round(degrees / QUARTER)) % 4
    c, s = [1, 0, -1, 0][turns], [0, 1, 0, -1][turns]
    if axis == "x":
        return [1, 0, 0, 0, c, -s, 0, s, c]
    if axis == "z":
        return [c, -s, 0, s, c, 0, 0, 0, 1]
    return [c, 0, s, 0, 1, 0, -s, 0, c]          # y, the one that matters


def placements(text):
    """Every submodel placed in the main model: ``{normalised name: line no}``.

    Only the main model's own body is read. A submodel that places another
    submodel is somebody's sub-assembly and not a thing the scene arranges.
    """
    found = {}
    seen_first_file = False
    in_main = True
    for number, line in enumerate(text.splitlines(), start=1):
        header = _FILE.match(line)
        if header:
            # The first `0 FILE` opens the main model; the next one closes it.
            in_main = not seen_first_file
            seen_first_file = True
            continue
        if not in_main:
            continue
        match = _PLACEMENT.match(line)
        if match:
            found.setdefault(_norm(match.group("name")), number)
    return found


def _read(line):
    match = _PLACEMENT.match(line)
    if not match:
        return None
    numbers = [float(v) for v in match.group("nums").split()]
    return match, numbers[:3], numbers[3:12]


def _write(match, position, matrix):
    numbers = " ".join(_num(v) for v in list(position) + list(matrix))
    return f"{match.group('lead')}{numbers} {match.group('name')}"


def _centre(text, name):
    """The middle of a submodel's own contents, in its local frame.

    Used as the pivot for a turn. Measured from the parts' own positions rather
    than their bounding boxes: this needs the middle of where the object *is*,
    which its placement coordinates give closely enough, and reading the
    geometry of every part to do better would mean loading the library to move
    one line.
    """
    wanted = _norm(name)
    inside = False
    xs, ys, zs = [], [], []
    for line in text.splitlines():
        header = _FILE.match(line)
        if header:
            inside = _norm(header.group(1)) == wanted
            continue
        if not inside:
            continue
        match = _PLACEMENT.match(line)
        if match:
            x, y, z = [float(v) for v in match.group("nums").split()][:3]
            xs.append(x); ys.append(y); zs.append(z)
    if not xs:
        return None
    return [(min(xs) + max(xs)) / 2.0,
            (min(ys) + max(ys)) / 2.0,
            (min(zs) + max(zs)) / 2.0]


def _lookup(where, name):
    """The key for ``name``, taking it with or without its .ldr suffix."""
    key = _norm(name)
    if key in where:
        return key
    stem = key.rsplit(".", 1)[0]
    for candidate in where:
        if candidate.rsplit(".", 1)[0] == stem:
            return candidate
    return None


def _target(text, name):
    """``(lines, index, match, position, matrix)`` for a submodel's placement."""
    lines = text.splitlines()
    where = placements(text)
    key = _lookup(where, name)
    if key is None:
        known = ", ".join(sorted(where)) or "none"
        return None, (f"no submodel called '{name}' is placed in this scene. "
                      f"It holds: {known}")
    number = where[key]
    parsed = _read(lines[number - 1])
    if parsed is None:
        return None, f"line {number} does not place anything"
    match, position, matrix = parsed
    return (lines, number - 1, match, position, matrix), None


def move(text, name, dx=0.0, dy=0.0, dz=0.0):
    """Shift a whole submodel. Returns ``(new_text, report)`` or ``(None, err)``."""
    found, error = _target(text, name)
    if error:
        return None, {"error": error}
    lines, index, match, position, matrix = found

    moved = [position[0] + float(dx), position[1] + float(dy),
             position[2] + float(dz)]
    lines[index] = _write(match, moved, matrix)
    return "\n".join(lines) + "\n", {
        "submodel": match.group("name"),
        "line": index + 1,
        "moved_by": {"x": float(dx), "y": float(dy), "z": float(dz)},
        "was_at": [round(v, 2) for v in position],
        "now_at": [round(v, 2) for v in moved],
    }


def rotate(text, name, degrees=90, axis="y"):
    """Turn a whole submodel about its own centre.

    About its centre, not its origin: a submodel's origin is wherever its
    builder happened to put it, so turning about that swings the object across
    the scene as well as facing it a different way. Turning a car to face the
    other way should leave the car where it is.
    """
    axis = (axis or "y").strip().lower()
    if axis not in ("x", "y", "z"):
        return None, {"error": "axis must be x, y or z"}
    if abs(float(degrees)) % QUARTER > 1e-6:
        return None, {"error": f"turns are multiples of {QUARTER} degrees - "
                               f"anything else takes the object off the grid"}

    found, error = _target(text, name)
    if error:
        return None, {"error": error}
    lines, index, match, position, matrix = found

    turn = _rotation(axis, float(degrees))
    turned = _mat_mul(turn, matrix)

    # Put it back over its own middle. The pivot is the submodel's centre
    # expressed in the scene, so the correction is where that centre lands
    # after the turn, subtracted from where it was.
    centre = _centre(text, match.group("name"))
    moved = list(position)
    if centre is not None:
        before = _mat_vec(matrix, centre)
        after = _mat_vec(turned, centre)
        moved = [position[k] + before[k] - after[k] for k in range(3)]

    lines[index] = _write(match, moved, turned)
    return "\n".join(lines) + "\n", {
        "submodel": match.group("name"),
        "line": index + 1,
        "turned": f"{int(round(float(degrees)))} degrees about {axis}",
        "was_at": [round(v, 2) for v in position],
        "now_at": [round(v, 2) for v in moved],
        "kept_over_its_centre": centre is not None,
    }


def summary(text):
    """What the scene holds and where each object sits - the arranging view."""
    lines = text.splitlines()
    out = []
    for name, number in sorted(placements(text).items(), key=lambda kv: kv[1]):
        parsed = _read(lines[number - 1])
        if parsed is None:
            continue
        match, position, matrix = parsed
        facing = None
        for degrees in (0, 90, 180, 270):
            if all(abs(a - b) < 1e-6
                   for a, b in zip(matrix, _rotation("y", degrees))):
                facing = degrees
                break
        out.append({
            "submodel": match.group("name"),
            "line": number,
            "at": [round(v, 2) for v in position],
            "turned": (f"{facing} degrees about y" if facing is not None
                       else "an angle of its own"),
        })
    return out
